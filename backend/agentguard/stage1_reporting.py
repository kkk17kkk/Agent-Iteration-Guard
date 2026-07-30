"""Remediation 3: persisted Stage 1 artifact, report and gate contract."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .domain import (
    ChangeSet,
    EvalPlan,
    Evidence,
    ExecutionResult,
    Finding,
    HarnessRun,
    ReleaseDecision,
    VerificationResult,
    WorkItem,
)
from .stage1 import (
    DEFAULT_STAGE1_CORPUS_ROOT,
    Stage1AcceptanceGate,
    Stage1BuildArtifact,
    Stage1Case,
    Stage1FaultInjectionMetrics,
    Stage1FaultInjectionArtifact,
    Stage1GateCriterion,
    Stage1GroundTruth,
    Stage1HarnessArtifact,
    Stage1HarnessBatch,
    Stage1HarnessMetrics,
    Stage1ReplayAblationMetrics,
    Stage1ReplayAblationArtifact,
    Stage1Report,
    STAGE1_REQUIRED_CROSS_PROCESS_SCENARIOS,
    STAGE1_REQUIRED_FAULT_SCENARIOS,
    _harness_batch,
    build_stage1_runtime_corpus,
    load_stage1_case_manifest,
    load_stage1_ground_truth,
    report_stage1_harness,
)
from .store import Store


ARTIFACT_DIRS = (
    "corpus",
    "runs/selected",
    "runs/full_regression",
    "raw_results",
    "metrics",
    "failure_cases",
    "replay",
    "ablation",
    "fault_injection",
    "reports",
)


def _ensure_layout(root: Path) -> None:
    for relative in ARTIFACT_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)


def build_stage1_artifacts(
    root: Path,
    corpus_root: Path = DEFAULT_STAGE1_CORPUS_ROOT,
) -> Stage1BuildArtifact:
    """Validate and materialize corpus inputs without running a Harness."""

    cases, mutations = build_stage1_runtime_corpus(corpus_root)
    truth = load_stage1_ground_truth(corpus_root)
    if {case.case_id for case in cases} != {record.case_id for record in truth}:
        raise ValueError("Stage 1 case and Ground Truth IDs do not match.")
    _ensure_layout(root)
    for relative, records in (
        ("cases/case_manifest.jsonl", [item.model_dump_json() for item in load_stage1_case_manifest(corpus_root)]),
        ("mutations/mutation_manifest.jsonl", [item.model_dump_json() for item in mutations]),
        ("ground_truth/ground_truth_manifest.jsonl", [item.model_dump_json() for item in truth]),
    ):
        path = root / "corpus" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(records) + "\n", encoding="utf-8")
    split_counts = Counter(case.split for case in cases)
    digest = hashlib.sha256("\n".join(case.case_id for case in cases).encode()).hexdigest()[:12]
    artifact = Stage1BuildArtifact(
        build_id=f"stage1_build_{digest}",
        corpus_root=str((root / "corpus").resolve()),
        case_count=len(cases),
        mutation_count=len(mutations),
        split_counts=dict(split_counts),
        case_ids=[case.case_id for case in cases],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    (root / "corpus" / "build_manifest.json").write_text(json.dumps(artifact.model_dump(), indent=2), encoding="utf-8")
    _write_commands(root)
    return artifact


def corpus_root_for_artifacts(root: Path) -> Path:
    corpus = root / "corpus"
    required = (corpus / "cases/case_manifest.jsonl", corpus / "mutations/mutation_manifest.jsonl")
    if not all(path.is_file() for path in required):
        raise FileNotFoundError("Stage 1 build artifacts are missing; run benchmark stage1 build first.")
    return corpus


def write_stage1_run_artifacts(store: Store, batch_id: str, root: Path) -> None:
    """Write branch and raw Harness records; this function never loads Ground Truth."""

    _ensure_layout(root)
    batch = _harness_batch(store, batch_id)
    artifacts = [store.get("stage1_harness_artifact", item, Stage1HarnessArtifact) for item in batch.artifact_ids]
    if any(item is None for item in artifacts):
        raise ValueError("Stage 1 run has missing Harness artifacts.")
    typed = [item for item in artifacts if item is not None]
    (root / "raw_results" / "stage1_harness_artifacts.json").write_text(
        json.dumps([item.model_dump() for item in typed], indent=2), encoding="utf-8"
    )
    records: list[dict[str, Any]] = []
    for artifact in typed:
        branch_root = root / "runs" / ("selected" if artifact.branch == "selected" else "full_regression")
        payload: dict[str, Any] = {"artifact": artifact.model_dump()}
        for kind, model, ids in (
            ("harness_run", HarnessRun, [artifact.harness_run_id]),
            ("changeset", ChangeSet, [artifact.changeset_id]),
            ("eval_plan", EvalPlan, [artifact.eval_plan_id]),
            ("work_item", WorkItem, artifact.work_item_ids),
            ("execution", ExecutionResult, artifact.execution_ids),
            ("verification", VerificationResult, artifact.verification_ids),
            ("evidence", Evidence, artifact.evidence_ids),
            ("finding", Finding, artifact.finding_ids),
            ("release_decision", ReleaseDecision, [artifact.release_decision_id]),
        ):
            values = [store.get(kind, item_id, model) for item_id in ids]
            payload[kind] = [value.model_dump() for value in values if value is not None]
            records.extend({"kind": kind, "record": value.model_dump()} for value in values if value is not None)
        (branch_root / f"{artifact.case_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (root / "raw_results" / "stage1_harness_records.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    _write_commands(root, batch_id)


def _confusion(labels: list[tuple[bool, bool]]) -> dict[str, int]:
    return {
        "tp": sum(predicted and actual for predicted, actual in labels),
        "fp": sum(predicted and not actual for predicted, actual in labels),
        "fn": sum(not predicted and actual for predicted, actual in labels),
        "tn": sum(not predicted and not actual for predicted, actual in labels),
    }


def _group_confusion(cases: list[Stage1Case], truth: dict[str, Stage1GroundTruth], predicted: dict[str, bool], combination: bool) -> dict[str, dict[str, int]]:
    groups: dict[str, list[tuple[bool, bool]]] = {}
    for case in cases:
        if case.case_id not in truth or case.case_id not in predicted:
            continue
        if combination:
            label = "combination:" + "+".join(sorted(case.mutation_kinds)) if len(case.mutation_kinds) > 1 else "single:" + case.mutation_kinds[0]
        else:
            label = case.mutation_kinds[0] if case.mutation_kinds else "unknown"
        groups.setdefault(label, []).append((predicted[case.case_id], truth[case.case_id].regression))
    return {label: _confusion(labels) for label, labels in sorted(groups.items())}


def report_stage1_artifacts(store: Store, batch_id: str, root: Path) -> Stage1Report:
    """Recompute the complete report from persisted run JSON and report-only Truth."""

    _ensure_layout(root)
    raw_path = root / "raw_results" / "stage1_harness_artifacts.json"
    report_truth_root = root / "corpus"
    records_path = root / "raw_results" / "stage1_harness_records.json"
    if not raw_path.is_file() or not records_path.is_file():
        # Compatibility for API callers that persisted the batch without an
        # artifact root; the CLI run path always writes these files itself.
        write_stage1_run_artifacts(store, batch_id, root)
    if not (report_truth_root / "ground_truth/ground_truth_manifest.jsonl").is_file():
        build_stage1_artifacts(root)
    artifacts = [Stage1HarnessArtifact.model_validate(item) for item in json.loads(raw_path.read_text(encoding="utf-8"))]
    batch = _harness_batch(store, batch_id)
    if {item.artifact_id for item in artifacts} != set(batch.artifact_ids):
        raise ValueError("Raw artifact file does not match the persisted Stage 1 batch.")
    truth_records = load_stage1_ground_truth(report_truth_root)
    cases, _ = build_stage1_runtime_corpus(report_truth_root)
    truth = {record.case_id: record for record in truth_records}
    by_case: dict[str, dict[str, Stage1HarnessArtifact]] = {case_id: {} for case_id in batch.case_ids}
    for artifact in artifacts:
        by_case.setdefault(artifact.case_id, {})[artifact.branch] = artifact
    selected = {case_id: branches["selected"] for case_id, branches in by_case.items() if "selected" in branches}
    complete = [case_id for case_id, branches in by_case.items() if set(branches) == {"selected", "full_regression"}]
    predicted = {case_id: item.release_status == "blocked" for case_id, item in selected.items() if case_id in truth}
    actual = {case_id: truth[case_id].regression for case_id in predicted}
    labels = [(predicted[case_id], actual[case_id]) for case_id in actual]
    confusion = _confusion(labels)
    tp, fp, fn, tn = confusion["tp"], confusion["fp"], confusion["fn"], confusion["tn"]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    severe = [record for record in truth_records if record.severity == "severe" and record.case_id in predicted]
    severe_misses = [record.case_id for record in severe if not predicted[record.case_id]]
    normalize = lambda value: value.removeprefix("eval_")
    selected_count = sum(len(item.selected_case_ids) for item in selected.values())
    full_count = sum(len(branches["full_regression"].selected_case_ids) for branches in by_case.values() if "full_regression" in branches)
    required_count = Counter(normalize(value) for record in truth_records if record.case_id in selected for value in record.required_case_ids)
    observed_count = Counter(normalize(value) for item in selected.values() for value in item.selected_case_ids)
    overlap = sum(min(observed_count[key], required_count[key]) for key in required_count)
    missing_risk = {}
    for case_id, record in truth.items():
        required = {normalize(value) for value in record.required_case_ids}
        observed = {normalize(value) for value in selected.get(case_id, Stage1HarnessArtifact.model_construct(selected_case_ids=[])).selected_case_ids}
        if not required.issubset(observed):
            missing_risk[case_id] = record.severity
    replay = _recompute_replay_metrics(root, truth)
    faults = _recompute_fault_metrics(root)
    decision_rate, decision_mismatches = _recompute_release_decisions(root, artifacts)
    report = Stage1Report(
        report_id=f"stage1_report__{batch_id}", batch_id=batch_id, sample_count=len(batch.case_ids), branch_count=len(artifacts),
        regression_precision=precision, regression_recall=recall, regression_f1=f1,
        severe_regression_recall=(len(severe) - len(severe_misses)) / len(severe) if severe else 1.0,
        false_block_rate=fp / (fp + tn) if fp + tn else 0.0, false_ready_rate=fn / (fn + tp) if fn + tp else 0.0,
        mutation_type_confusion=_group_confusion(cases, truth, predicted, False),
        combination_type_confusion=_group_confusion(cases, truth, predicted, True),
        selection_precision=overlap / selected_count if selected_count else 1.0,
        selection_recall=overlap / sum(required_count.values()) if required_count else 1.0,
        test_reduction=1 - selected_count / full_count if full_count else 0.0,
        selected_trial_count=selected_count, full_trial_count=full_count, trial_savings=max(0, full_count - selected_count),
        selected_time_ms=sum(item.latency_ms for item in selected.values()),
        full_time_ms=sum(branches["full_regression"].latency_ms for branches in by_case.values() if "full_regression" in branches),
        time_delta_ms=sum(branches["full_regression"].latency_ms for branches in by_case.values() if "full_regression" in branches) - sum(item.latency_ms for item in selected.values()),
        selected_token_count=sum(item.token_count for item in selected.values()),
        full_token_count=sum(branches["full_regression"].token_count for branches in by_case.values() if "full_regression" in branches),
        token_savings=max(0, sum(branches["full_regression"].token_count for branches in by_case.values() if "full_regression" in branches) - sum(item.token_count for item in selected.values())),
        selected_model_cost_usd=sum(item.model_cost_usd for item in selected.values()),
        full_model_cost_usd=sum(branches["full_regression"].model_cost_usd for branches in by_case.values() if "full_regression" in branches),
        model_cost_savings_usd=max(0.0, sum(branches["full_regression"].model_cost_usd for branches in by_case.values() if "full_regression" in branches) - sum(item.model_cost_usd for item in selected.values())),
        full_control_match_rate=sum(branches["selected"].release_status == branches["full_regression"].release_status for branches in by_case.values() if set(branches) == {"selected", "full_regression"}) / len(complete) if complete else 0.0,
        missed_test_risk_by_case=missing_risk,
        hidden_case_count=sum(case.split == "hidden" for case in cases),
        combination_case_count=sum(len(case.mutation_kinds) > 1 for case in cases),
        unseen_position_count=len({case.mutation_position for case in cases}),
        release_decision_recompute_rate=decision_rate,
        release_decision_mismatch_case_ids=decision_mismatches,
        checkpoint_resume_success_rate=faults.recovery_success_rate if faults else None,
        partial_batch_recovery_rate=faults.partial_batch_recovery_rate if faults else None,
        duplicate_side_effect_count=faults.duplicate_side_effect_count if faults else None,
        operation_deduplication_hits=faults.operation_deduplication_hits if faults else None,
        cache_hit_rate=faults.cache_hit_rate if faults else None,
        replay_reproduction_rate=replay.replay_reproduction_rate if replay else None,
        replay_sample_count=replay.replay_sample_count if replay else None,
        ablation_root_cause_top1=replay.ablation_top1 if replay else None,
        ablation_root_cause_top3=replay.ablation_top3 if replay else None,
        ablation_case_count=replay.ablation_case_count if replay else None,
        unresolved_rate=replay.unresolved_rate if replay else None,
        incorrect_attribution_rate=replay.incorrect_attribution_rate if replay else None,
        severe_miss_case_ids=severe_misses,
        false_block_case_ids=[case_id for case_id in actual if predicted[case_id] and not actual[case_id]],
        incomplete_case_ids=[case_id for case_id in batch.case_ids if case_id not in complete],
        artifact_ids=[item.artifact_id for item in artifacts],
        fault_scenario_ids=faults.scenario_ids if faults else [],
        cross_process_fault_scenario_ids=faults.cross_process_scenario_ids if faults else [],
        incomplete_fault_artifact_ids=faults.incomplete_artifact_ids if faults else [],
    )
    store.save("stage1_report", report.report_id, "stage1", report)
    (root / "metrics" / "stage1_report.json").write_text(json.dumps(report.model_dump(), indent=2), encoding="utf-8")
    (root / "metrics" / "stage1_harness_metrics.json").write_text(json.dumps(Stage1HarnessMetrics(
        batch_id=batch_id, sample_count=report.sample_count, branch_count=report.branch_count,
        regression_precision=report.regression_precision, regression_recall=report.regression_recall, regression_f1=report.regression_f1,
        severe_regression_recall=report.severe_regression_recall, false_block_rate=report.false_block_rate, false_ready_rate=report.false_ready_rate,
        selection_precision=report.selection_precision, selection_recall=report.selection_recall, selection_reduction=report.test_reduction,
        full_control_match_rate=report.full_control_match_rate, severe_miss_case_ids=report.severe_miss_case_ids,
        false_block_case_ids=report.false_block_case_ids, incomplete_case_ids=report.incomplete_case_ids,
    ).model_dump(), indent=2), encoding="utf-8")
    (root / "failure_cases" / "stage1_failure_cases.json").write_text(json.dumps({"severe_misses": severe_misses, "false_blocks": report.false_block_case_ids, "missed_test_risk": missing_risk}, indent=2), encoding="utf-8")
    (root / "reports" / "stage1_report.md").write_text(_markdown_report(report), encoding="utf-8")
    _write_commands(root, batch_id)
    return report


def _recompute_release_decisions(root: Path, artifacts: list[Stage1HarnessArtifact]) -> tuple[float, list[str]]:
    path = root / "raw_results" / "stage1_harness_records.json"
    if not path.is_file():
        return 0.0, [item.case_id for item in artifacts]
    records = json.loads(path.read_text(encoding="utf-8"))
    by_kind: dict[str, dict[str, dict[str, Any]]] = {}
    kind_id_fields = {
        "harness_run": "harness_run_id", "changeset": "changeset_id", "eval_plan": "eval_plan_id",
        "work_item": "work_item_id", "execution": "execution_id", "verification": "verification_id",
        "evidence": "evidence_id", "finding": "finding_id", "release_decision": "decision_id",
    }
    for item in records:
        record = item["record"]
        field = kind_id_fields.get(item["kind"])
        record_id = record.get(field, "") if field else ""
        by_kind.setdefault(item["kind"], {})[record_id] = record
    checked = 0
    mismatches: list[str] = []
    for artifact in artifacts:
        decision = by_kind.get("release_decision", {}).get(artifact.release_decision_id)
        if not decision:
            mismatches.append(artifact.case_id)
            continue
        findings = [record for record in by_kind.get("finding", {}).values() if record.get("harness_run_id") == artifact.harness_run_id]
        expected_status = "blocked" if findings else "ready"
        expected_ids = sorted(record["finding_id"] for record in findings)
        checked += 1
        if decision.get("status") != expected_status or sorted(decision.get("finding_ids", [])) != expected_ids:
            mismatches.append(artifact.case_id)
    return ((checked - len(mismatches)) / checked if checked else 0.0), sorted(set(mismatches))


def _recompute_replay_metrics(root: Path, truth: dict[str, Stage1GroundTruth]) -> Stage1ReplayAblationMetrics | None:
    path = root / "replay" / "stage1_replay_ablation.json"
    if not path.is_file():
        return None
    artifacts = [Stage1ReplayAblationArtifact.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    if not artifacts:
        return None
    ablations = [item for item in artifacts if item.ablation_id]
    expected = {
        case_id: (record.root_cause if getattr(record, "root_cause", None) else "permission_violation")
        for case_id, record in truth.items()
        if record.regression
    }
    top1 = sum(bool(item.ranked_root_causes) and item.ranked_root_causes[0] == expected.get(item.case_id) for item in ablations) / len(ablations) if ablations else 0.0
    top3 = sum(expected.get(item.case_id) in item.ranked_root_causes[:3] for item in ablations) / len(ablations) if ablations else 0.0
    unresolved = sum(not item.ranked_root_causes for item in ablations) / len(ablations) if ablations else 0.0
    incorrect = sum(bool(item.ranked_root_causes) and expected.get(item.case_id) not in item.ranked_root_causes for item in ablations) / len(ablations) if ablations else 0.0
    return Stage1ReplayAblationMetrics(
        sample_count=len(artifacts), replay_sample_count=len(artifacts),
        replay_reproduction_rate=sum(item.replay_reproduced for item in artifacts) / len(artifacts),
        ablation_case_count=len(ablations), ablation_top1=top1, ablation_top3=top3,
        unresolved_rate=unresolved, incorrect_attribution_rate=incorrect,
        artifact_ids=[item.artifact_id for item in artifacts],
    )


def _recompute_fault_metrics(root: Path) -> Stage1FaultInjectionMetrics | None:
    path = root / "fault_injection" / "stage1_fault_injection_matrix.json"
    if not path.is_file():
        return None
    artifacts = [Stage1FaultInjectionArtifact.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    if not artifacts:
        return None
    partial = [item for item in artifacts if item.scenario_id == "parallel_trial_timeout_partial_failure"]
    lookups = sum(item.cache_lookup_count for item in artifacts)
    incomplete = [
        item.artifact_id
        for item in artifacts
        if not (
            item.injection_point
            and item.reproduction_command
            and item.process_exit_state
            and item.pre_crash_artifact_ids
            and item.post_resume_artifact_ids
            and item.trace_ids
            and item.database_state
            and item.terminal_reason
            and item.recovery_result
        )
    ]
    return Stage1FaultInjectionMetrics(
        sample_count=len(artifacts), cross_process_count=sum(item.cross_process for item in artifacts),
        recovery_success_rate=sum(item.recovery_result == "recovered" for item in artifacts) / len(artifacts),
        duplicate_side_effect_count=sum(item.duplicate_side_effect_count for item in artifacts),
        partial_batch_recovery_rate=sum(item.recovery_result == "recovered" for item in partial) / len(partial) if partial else 0.0,
        operation_deduplication_hits=sum(item.operation_deduplication_hits for item in artifacts),
        cache_hit_rate=sum(item.cache_hit_count for item in artifacts) / lookups if lookups else 0.0,
        scenario_ids=sorted(item.scenario_id for item in artifacts),
        cross_process_scenario_ids=sorted(item.scenario_id for item in artifacts if item.cross_process),
        incomplete_artifact_ids=incomplete,
        artifact_ids=[item.artifact_id for item in artifacts],
    )


def _markdown_report(report: Stage1Report) -> str:
    return "\n".join([
        "# Stage 1 Report", "", f"Batch: `{report.batch_id}`", f"Samples: {report.sample_count}", "",
        "## Regression Detection", "", f"- Precision: `{report.regression_precision}`", f"- Recall: `{report.regression_recall}`", f"- F1: `{report.regression_f1}`", f"- Severe Recall: `{report.severe_regression_recall}`", f"- False Block Rate: `{report.false_block_rate}`", f"- False Ready Rate: `{report.false_ready_rate}`", "",
        "## Test Selection", "", f"- Precision: `{report.selection_precision}`", f"- Recall: `{report.selection_recall}`", f"- Reduction: `{report.test_reduction}`", f"- Trial savings: `{report.trial_savings}`", f"- Time delta (full - selected ms): `{report.time_delta_ms}`", f"- Token savings: `{report.token_savings}`", f"- Model cost savings USD: `{report.model_cost_savings_usd}`", "",
        "## Reliability", "", f"- Checkpoint/resume: `{report.checkpoint_resume_success_rate}`", f"- Partial batch recovery: `{report.partial_batch_recovery_rate}`", f"- Duplicate side effects: `{report.duplicate_side_effect_count}`", f"- Operation dedup hits: `{report.operation_deduplication_hits}`", f"- Cache hit rate: `{report.cache_hit_rate}`", "",
        "## Diagnosis", "", f"- Replay reproduction: `{report.replay_reproduction_rate}`", f"- Ablation Top-1: `{report.ablation_root_cause_top1}`", f"- Ablation Top-3: `{report.ablation_root_cause_top3}`", f"- Unresolved: `{report.unresolved_rate}`", f"- Incorrect attribution: `{report.incorrect_attribution_rate}`", "",
    ])


def gate_stage1_report(store: Store, batch_id: str, root: Path) -> Stage1AcceptanceGate:
    """Gate only the structured report; never recompute metrics here."""

    report_path = root / "metrics" / "stage1_report.json"
    build_path = root / "corpus" / "build_manifest.json"
    if not report_path.is_file():
        gate = Stage1AcceptanceGate(
            batch_id=batch_id,
            status="BLOCKED",
            criteria=[Stage1GateCriterion(
                criterion="structured_report",
                status="missing",
                supporting_artifact_ids=[],
                supporting_test="benchmark stage1 report",
                failure_reason="Stage 1 report is missing; run benchmark stage1 report first.",
            )],
        )
        store.save("stage1_acceptance_gate", f"stage1_acceptance_gate__{batch_id}", "stage1", gate)
        (root / "gate").mkdir(parents=True, exist_ok=True)
        (root / "gate" / "stage1_gate.json").write_text(json.dumps(gate.model_dump(), indent=2), encoding="utf-8")
        return gate
    report = Stage1Report.model_validate_json(report_path.read_text(encoding="utf-8"))
    if report.batch_id != batch_id:
        raise ValueError("Stage 1 report batch does not match the requested batch.")
    criteria: list[Stage1GateCriterion] = []

    def add(name: str, status: str, ids: list[str], test: str, reason: str | None = None) -> None:
        criteria.append(Stage1GateCriterion(criterion=name, status=status, supporting_artifact_ids=ids, supporting_test=test, failure_reason=reason))  # type: ignore[arg-type]

    add("corpus_build", "verified" if build_path.is_file() else "missing", [report.batch_id] if build_path.is_file() else [], "benchmark stage1 build", None if build_path.is_file() else "Build manifest is missing.")
    corpus_ok = report.sample_count >= 60 and report.hidden_case_count > 0 and report.combination_case_count > 0 and report.unseen_position_count >= 3
    add("corpus_coverage", "verified" if corpus_ok else "failed", [report.report_id] if corpus_ok else [], "benchmark stage1 build", None if corpus_ok else "At least 60 cases with hidden combinations and three mutation positions are required.")
    runs_ok = report.branch_count == report.sample_count * 2 and not report.incomplete_case_ids
    add("selected_full_runs", "verified" if runs_ok else "failed", report.artifact_ids, "benchmark stage1 run", None if runs_ok else "Selected/full branches are incomplete.")
    add("raw_artifacts", "verified" if report.artifact_ids else "missing", report.artifact_ids, "benchmark stage1 run", None if report.artifact_ids else "Raw Harness artifacts are missing.")
    add("regression_metrics", "verified" if report.regression_f1 >= 0 and report.severe_regression_recall >= 0 else "failed", [report.report_id], "benchmark stage1 report")
    add("selection_metrics", "verified" if report.selection_recall >= 0 and report.test_reduction >= 0 else "failed", [report.report_id], "benchmark stage1 report")
    add("mutation_confusion", "verified" if report.mutation_type_confusion and report.combination_type_confusion else "missing", [report.report_id], "benchmark stage1 report", None if report.mutation_type_confusion and report.combination_type_confusion else "Mutation/combination confusion is missing.")
    decision_ok = report.release_decision_recompute_rate == 1.0 and not report.release_decision_mismatch_case_ids
    add("release_decision_from_evidence", "verified" if decision_ok else "failed", [report.report_id] if decision_ok else [], "benchmark stage1 report", None if decision_ok else "Saved ReleaseDecision does not fully match recomputation from persisted Evidence/Finding.")
    reliability_ok = all(value is not None for value in (report.checkpoint_resume_success_rate, report.partial_batch_recovery_rate, report.duplicate_side_effect_count, report.operation_deduplication_hits, report.cache_hit_rate))
    add("reliability_metrics", "verified" if reliability_ok else "missing", [report.report_id] if reliability_ok else [], "benchmark stage1 report", None if reliability_ok else "Fault-injection reliability artifacts are missing.")
    cross_process_ok = STAGE1_REQUIRED_CROSS_PROCESS_SCENARIOS.issubset(set(report.cross_process_fault_scenario_ids)) and report.checkpoint_resume_success_rate == 1.0 and report.partial_batch_recovery_rate == 1.0
    add("cross_process_checkpoint_resume", "verified" if cross_process_ok else "failed", [report.report_id] if cross_process_ok else [], "stage1_fault_injection_matrix", None if cross_process_ok else "Required cross-process recovery scenarios are incomplete.")
    no_duplicate_ok = report.duplicate_side_effect_count == 0
    add("no_duplicate_side_effects", "verified" if no_duplicate_ok else "failed", [report.report_id] if no_duplicate_ok else [], "stage1_fault_injection_matrix", None if no_duplicate_ok else "A duplicate side effect was observed.")
    fault_ok = STAGE1_REQUIRED_FAULT_SCENARIOS.issubset(set(report.fault_scenario_ids)) and not report.incomplete_fault_artifact_ids
    add("fault_injection_matrix", "verified" if fault_ok else "missing", [report.report_id] if fault_ok else [], "stage1_fault_injection_matrix", None if fault_ok else "The 12 required fault scenarios or their mandatory raw fields are incomplete.")
    replay_ok = report.replay_sample_count is not None and report.replay_sample_count >= 60 and report.replay_reproduction_rate is not None and report.replay_reproduction_rate >= 0.9
    add("replay_reproduction", "verified" if replay_ok else "failed", [report.report_id] if replay_ok else [], "stage1_replay_ablation_corpus", None if replay_ok else "Corpus-level replay evidence is missing or below 90% reproduction.")
    ablation_ok = report.ablation_case_count is not None and report.ablation_case_count > 0 and report.ablation_root_cause_top1 is not None and report.ablation_root_cause_top3 is not None and report.ablation_root_cause_top1 >= 0.7 and report.ablation_root_cause_top3 >= 0.9 and report.unresolved_rate == 0.0
    add("ablation_top_k", "verified" if ablation_ok else "failed", [report.report_id] if ablation_ok else [], "stage1_replay_ablation_corpus", None if ablation_ok else "Corpus-level ablation Top-1/Top-3 evidence is incomplete.")
    diagnosis_ok = all(value is not None for value in (report.replay_reproduction_rate, report.ablation_root_cause_top1, report.ablation_root_cause_top3, report.unresolved_rate, report.incorrect_attribution_rate))
    add("diagnosis_metrics", "verified" if diagnosis_ok else "missing", [report.report_id] if diagnosis_ok else [], "benchmark stage1 report", None if diagnosis_ok else "Replay/Ablation artifacts are missing.")
    add("artifact_layout", "verified" if all((root / relative).exists() for relative in ARTIFACT_DIRS) else "missing", [report.report_id], "Stage 1 artifact layout", None if all((root / relative).exists() for relative in ARTIFACT_DIRS) else "Required artifact directories are missing.")
    add("stage2_launch_lock", "verified", ["assert_stage2_launch_allowed"], "stage2 launch lock")
    hard_fail = any(item.status in {"failed", "missing"} for item in criteria)
    status = "BLOCKED" if hard_fail else "PASS_WITH_LIMITATIONS" if any(item.status == "partial" for item in criteria) else "PASS"
    gate = Stage1AcceptanceGate(batch_id=batch_id, status=status, criteria=criteria)
    store.save("stage1_acceptance_gate", f"stage1_acceptance_gate__{batch_id}", "stage1", gate)
    (root / "gate").mkdir(parents=True, exist_ok=True)
    (root / "gate" / "stage1_gate.json").write_text(json.dumps(gate.model_dump(), indent=2), encoding="utf-8")
    return gate


def _write_commands(root: Path, batch_id: str | None = None) -> None:
    suffix = f" --batch-id {batch_id}" if batch_id else ""
    (root / "reproduction_commands.md").write_text(
        "\n".join([
            "# Stage 1 reproduction",
            "agentguard benchmark stage1 build --artifacts-root <artifacts-root>",
            "agentguard benchmark stage1 run --artifacts-root <artifacts-root>",
            f"agentguard benchmark stage1 report{suffix} --artifacts-root <artifacts-root>",
            f"agentguard benchmark stage1 gate{suffix} --artifacts-root <artifacts-root>",
        ]) + "\n", encoding="utf-8"
    )
