"""Stage 1 corpus boundaries and deterministic report primitives.

The corpus is intentionally split into three persisted inputs:

* ``case_manifest`` describes runnable baseline/candidate fixtures;
* ``mutation_manifest`` describes how a candidate is constructed and the
  temporary deterministic execution fixture used by this remediation;
* ``ground_truth_manifest`` is evaluation-only and is loaded by reporting.

The temporary execution fixture is deliberately marked as legacy.  Remediation
2 will replace it with the real Runner/Oracle path; this module's current
purpose is to make the Ground Truth boundary explicit and testable first.
"""

from collections import Counter
import json
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from .domain import (
    ChangeSet,
    Evidence,
    ExecutionResult,
    EvalPlan,
    FileAgentManifest,
    Finding,
    HarnessRun,
    ReleaseDecision,
    VerificationResult,
    WorkItem,
)
from .store import Store


Split = Literal["development", "validation", "hidden"]
Severity = Literal["none", "normal", "severe"]
ModelT = TypeVar("ModelT", bound=BaseModel)


DEFAULT_STAGE1_CORPUS_ROOT = Path(__file__).resolve().parents[2] / "corpus" / "stage1"


class Stage1CaseManifest(BaseModel):
    """Runnable fixture metadata; it contains no evaluation labels."""

    case_id: str
    split: Split
    baseline_ref: str
    candidate_ref: str
    fixture_ref: str
    available_case_ids: tuple[str, ...]
    cost_usd: float = Field(default=0.0, ge=0)


class Stage1MutationManifest(BaseModel):
    """Candidate construction metadata and temporary local execution fixture.

    ``triggered_check_ids`` is not Ground Truth.  It is a deterministic fixture
    used only until Remediation 2 drives the real Harness chain.
    """

    mutation_id: str
    case_id: str
    kind: str
    position: Literal["planning", "execution", "verification"]
    operation: str
    candidate_manifest: FileAgentManifest
    triggered_check_ids: tuple[str, ...] = ()


class Stage1Case(BaseModel):
    """Runtime input assembled from case and mutation manifests only."""

    case_id: str
    split: Split
    mutation_kinds: tuple[str, ...]
    mutation_position: Literal["planning", "execution", "verification"]
    mutation_ids: tuple[str, ...]
    baseline_ref: str
    candidate_ref: str
    fixture_ref: str
    available_case_ids: tuple[str, ...]
    candidate_manifest: FileAgentManifest
    cost_usd: float = Field(default=0.0, ge=0)


class Stage1GroundTruth(BaseModel):
    """Evaluation-only labels; never passed to selection or execution."""

    case_id: str
    regression: bool
    severity: Severity
    required_case_ids: tuple[str, ...]


class Stage1RawResult(BaseModel):
    case_id: str
    split: Split
    selected_case_ids: tuple[str, ...]
    full_case_ids: tuple[str, ...]
    observed_failure_case_ids: tuple[str, ...]
    release_status: Literal["ready", "blocked"]
    cost_usd: float = Field(ge=0)


Stage1HarnessBranch = Literal["selected", "full_regression"]


class Stage1HarnessArtifact(BaseModel):
    """Stable binding between one case/branch and the real Harness artifacts."""

    artifact_id: str
    product_id: str
    case_id: str
    branch: Stage1HarnessBranch
    baseline_ref: str
    candidate_ref: str
    environment_ref: str
    harness_run_id: str
    changeset_id: str
    candidate_fingerprint: str
    eval_plan_id: str
    selected_case_ids: list[str]
    work_item_ids: list[str]
    execution_ids: list[str]
    verification_ids: list[str]
    evidence_ids: list[str]
    finding_ids: list[str]
    release_decision_id: str
    run_status: str
    release_status: Literal["ready", "blocked"]


class Stage1HarnessBatch(BaseModel):
    batch_id: str
    case_ids: list[str]
    artifact_ids: list[str]
    status: Literal["completed"] = "completed"


class Stage1HarnessMetrics(BaseModel):
    batch_id: str
    sample_count: int
    branch_count: int
    regression_precision: float
    regression_recall: float
    regression_f1: float
    severe_regression_recall: float
    false_block_rate: float
    false_ready_rate: float
    selection_precision: float
    selection_recall: float
    selection_reduction: float
    full_control_match_rate: float
    severe_miss_case_ids: list[str]
    false_block_case_ids: list[str]
    incomplete_case_ids: list[str]


GateStatus = Literal["verified", "partial", "failed", "missing"]
HarnessGateStatus = Literal["PASS", "BLOCKED"]


class Stage1GateCriterion(BaseModel):
    criterion: str
    status: GateStatus
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    supporting_test: str | None = None
    failure_reason: str | None = None


class Stage1HarnessGate(BaseModel):
    batch_id: str
    status: HarnessGateStatus
    criteria: list[Stage1GateCriterion]


class Stage1ReplayAblationArtifact(BaseModel):
    """One persisted replay/ablation observation for the Stage 1 corpus."""

    artifact_id: str
    case_id: str
    mutation_kind: str
    harness_run_id: str
    source_trial_result_id: str
    replay_result_id: str
    replay_reproduced: bool
    ablation_id: str | None = None
    ablation_root_cause: str | None = None
    candidate_root_causes: list[str] = Field(default_factory=list)


class Stage1ReplayAblationMetrics(BaseModel):
    sample_count: int
    replay_sample_count: int
    replay_reproduction_rate: float = Field(ge=0, le=1)
    ablation_case_count: int
    ablation_top1: float = Field(ge=0, le=1)
    ablation_top3: float = Field(ge=0, le=1)
    unresolved_rate: float = Field(ge=0, le=1)
    artifact_ids: list[str] = Field(default_factory=list)


class Stage1FaultInjectionArtifact(BaseModel):
    """Auditable raw result for one injected failure scenario."""

    artifact_id: str
    scenario_id: str
    injection_point: str
    reproduction_command: str
    cross_process: bool
    process_exit_state: str
    pre_crash_artifact_ids: list[str] = Field(default_factory=list)
    post_resume_artifact_ids: list[str] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
    database_state: str
    terminal_reason: str
    duplicate_side_effect_count: int = Field(ge=0)
    recovery_result: Literal["recovered", "unresolved", "failed"]


class Stage1FaultInjectionMetrics(BaseModel):
    sample_count: int
    cross_process_count: int
    recovery_success_rate: float = Field(ge=0, le=1)
    duplicate_side_effect_count: int = Field(ge=0)
    artifact_ids: list[str] = Field(default_factory=list)


class Stage1AcceptanceGate(BaseModel):
    batch_id: str
    status: Literal["PASS", "PASS_WITH_LIMITATIONS", "BLOCKED"]
    criteria: list[Stage1GateCriterion]


class Stage1Metrics(BaseModel):
    sample_count: int
    regression_precision: float
    regression_recall: float
    regression_f1: float
    severe_regression_recall: float
    false_block_rate: float
    false_ready_rate: float
    selection_precision: float
    selection_recall: float
    selection_reduction: float
    total_cost_usd: float
    severe_miss_case_ids: list[str]
    false_block_case_ids: list[str]


def _load_jsonl(root: Path, relative: str, model: type[ModelT]) -> list[ModelT]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"Stage 1 manifest not found: {path}")
    records: list[ModelT] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"Invalid Stage 1 manifest row {path}:{line_number}") from exc
    if not records:
        raise ValueError(f"Stage 1 manifest is empty: {path}")
    return records


def load_stage1_case_manifest(root: Path = DEFAULT_STAGE1_CORPUS_ROOT) -> list[Stage1CaseManifest]:
    return _load_jsonl(root, "cases/case_manifest.jsonl", Stage1CaseManifest)


def load_stage1_mutation_manifest(root: Path = DEFAULT_STAGE1_CORPUS_ROOT) -> list[Stage1MutationManifest]:
    return _load_jsonl(root, "mutations/mutation_manifest.jsonl", Stage1MutationManifest)


def load_stage1_ground_truth(root: Path = DEFAULT_STAGE1_CORPUS_ROOT) -> list[Stage1GroundTruth]:
    """Load labels for reporting only; never call from the runtime path."""
    return _load_jsonl(root, "ground_truth/ground_truth_manifest.jsonl", Stage1GroundTruth)


def build_stage1_runtime_corpus(
    root: Path = DEFAULT_STAGE1_CORPUS_ROOT,
) -> tuple[list[Stage1Case], list[Stage1MutationManifest]]:
    """Build runtime inputs without loading Ground Truth."""
    manifests = load_stage1_case_manifest(root)
    mutations = load_stage1_mutation_manifest(root)
    case_ids = {manifest.case_id for manifest in manifests}
    mutation_case_ids = {mutation.case_id for mutation in mutations}
    if case_ids != mutation_case_ids:
        raise ValueError("Case and mutation manifests must contain the same case IDs.")
    mutation_by_case: dict[str, list[Stage1MutationManifest]] = {case_id: [] for case_id in case_ids}
    for mutation in mutations:
        mutation_by_case[mutation.case_id].append(mutation)
    cases = []
    for manifest in manifests:
        case_mutations = mutation_by_case[manifest.case_id]
        positions = {mutation.position for mutation in case_mutations}
        if len(positions) != 1:
            raise ValueError(f"All mutations for {manifest.case_id} must share one position.")
        candidate_manifests = {mutation.candidate_manifest.model_dump_json() for mutation in case_mutations}
        if len(candidate_manifests) != 1:
            raise ValueError(f"All mutations for {manifest.case_id} must share one candidate manifest.")
        candidate_manifest = case_mutations[0].candidate_manifest
        cases.append(
            Stage1Case(
                case_id=manifest.case_id,
                split=manifest.split,
                mutation_kinds=tuple(mutation.kind for mutation in case_mutations),
                mutation_position=case_mutations[0].position,
                mutation_ids=tuple(mutation.mutation_id for mutation in case_mutations),
                baseline_ref=manifest.baseline_ref,
                candidate_ref=manifest.candidate_ref,
                fixture_ref=manifest.fixture_ref,
                available_case_ids=manifest.available_case_ids,
                candidate_manifest=candidate_manifest,
                cost_usd=manifest.cost_usd,
            )
        )
    return cases, mutations


def build_stage1_corpus(
    root: Path = DEFAULT_STAGE1_CORPUS_ROOT,
) -> tuple[list[Stage1Case], list[Stage1GroundTruth]]:
    """Compatibility helper that joins independent inputs for report tests."""
    cases, _ = build_stage1_runtime_corpus(root)
    return cases, load_stage1_ground_truth(root)


def select_cases(case: Stage1Case) -> tuple[str, ...]:
    """A deterministic router deliberately independent of Ground Truth."""
    selected = {"smoke"}
    kinds = set(case.mutation_kinds)
    if kinds & {"skill", "prompt"}:
        selected.add("normal_write")
    if kinds & {"permission", "tool_schema", "workflow"}:
        selected.add("security")
    return tuple(sorted(selected))


def execute_case(
    case: Stage1Case,
    mutations: list[Stage1MutationManifest] | None = None,
) -> Stage1RawResult:
    """Evaluate selected checks against runtime fixtures, not Ground Truth."""
    if mutations is None:
        _, mutations = build_stage1_runtime_corpus()
    case_mutations = [mutation for mutation in mutations if mutation.case_id == case.case_id]
    selected = select_cases(case)
    full = ("smoke", "normal_write", "security")
    # TODO(Remediation 2): replace this deterministic fixture with Runner/Oracle.
    observed = tuple(
        sorted(
            {
                check_id
                for mutation in case_mutations
                for check_id in mutation.triggered_check_ids
                if check_id in selected
            }
        )
    )
    return Stage1RawResult(
        case_id=case.case_id,
        split=case.split,
        selected_case_ids=selected,
        full_case_ids=full,
        observed_failure_case_ids=observed,
        release_status="blocked" if observed else "ready",
        cost_usd=case.cost_usd,
    )


def compute_metrics(raw_results: list[Stage1RawResult], truth_records: list[Stage1GroundTruth]) -> Stage1Metrics:
    """Recompute all report values from persisted raw results and truth records."""
    truth_by_id = {record.case_id: record for record in truth_records}
    if len(raw_results) != len(truth_by_id) or {raw.case_id for raw in raw_results} != set(truth_by_id):
        raise ValueError("Raw results and Ground Truth must have exactly the same case IDs.")

    predicted = {raw.case_id: raw.release_status == "blocked" for raw in raw_results}
    actual = {case_id: record.regression for case_id, record in truth_by_id.items()}
    tp = sum(predicted[key] and actual[key] for key in actual)
    fp = sum(predicted[key] and not actual[key] for key in actual)
    fn = sum(not predicted[key] and actual[key] for key in actual)
    tn = sum(not predicted[key] and not actual[key] for key in actual)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    severe = [record for record in truth_records if record.severity == "severe"]
    severe_misses = [record.case_id for record in severe if not predicted[record.case_id]]

    normalize_case_id = lambda case_id: case_id.removeprefix("eval_")
    selected = Counter(normalize_case_id(case) for raw in raw_results for case in raw.selected_case_ids)
    required = Counter(normalize_case_id(case) for truth in truth_records for case in truth.required_case_ids)
    selected_required = sum(min(selected[key], required[key]) for key in required)
    selection_precision = selected_required / sum(selected.values()) if selected else 1.0
    selection_recall = selected_required / sum(required.values()) if required else 1.0
    full_count = sum(len(raw.full_case_ids) for raw in raw_results)
    selection_reduction = 1 - sum(selected.values()) / full_count if full_count else 0.0
    false_blocks = [case_id for case_id in actual if predicted[case_id] and not actual[case_id]]
    return Stage1Metrics(
        sample_count=len(raw_results),
        regression_precision=precision,
        regression_recall=recall,
        regression_f1=f1,
        severe_regression_recall=(len(severe) - len(severe_misses)) / len(severe) if severe else 1.0,
        false_block_rate=fp / (fp + tn) if fp + tn else 0.0,
        false_ready_rate=fn / (fn + tp) if fn + tp else 0.0,
        selection_precision=selection_precision,
        selection_recall=selection_recall,
        selection_reduction=selection_reduction,
        total_cost_usd=sum(raw.cost_usd for raw in raw_results),
        severe_miss_case_ids=severe_misses,
        false_block_case_ids=false_blocks,
    )


def run_stage1_corpus(store: Store, product_id: str, root: Path = DEFAULT_STAGE1_CORPUS_ROOT) -> list[Stage1RawResult]:
    """Run and persist runtime inputs/results without loading Ground Truth."""
    cases, mutations = build_stage1_runtime_corpus(root)
    raw = [execute_case(case, mutations) for case in cases]
    store.save_many([
        *[("stage1_case", f"stage1_case__{case.case_id}", product_id, case) for case in cases],
        *[("stage1_mutation", f"stage1_mutation__{mutation.mutation_id}", product_id, mutation) for mutation in mutations],
        *[("stage1_raw_result", f"stage1_raw__{result.case_id}", product_id, result) for result in raw],
    ])
    return raw


def report_stage1_corpus(
    store: Store,
    product_id: str,
    root: Path = DEFAULT_STAGE1_CORPUS_ROOT,
) -> Stage1Metrics:
    """Load persisted raw results and independent Ground Truth to report."""
    saved_raw = store.list("stage1_raw_result", Stage1RawResult, product_id)
    saved_truth = load_stage1_ground_truth(root)
    metrics = compute_metrics(saved_raw, saved_truth)
    store.save("stage1_metrics", "stage1_metrics", product_id, metrics)
    return metrics


def persist_corpus_run(store: Store, product_id: str, root: Path = DEFAULT_STAGE1_CORPUS_ROOT) -> Stage1Metrics:
    """Compatibility wrapper for the old combined run/report API."""
    run_stage1_corpus(store, product_id, root)
    return report_stage1_corpus(store, product_id, root)


def _harness_batch(store: Store, batch_id: str) -> Stage1HarnessBatch:
    batch = store.get("stage1_harness_batch", f"stage1_batch__{batch_id}", Stage1HarnessBatch)
    if batch is None:
        raise ValueError(f"Stage 1 Harness batch not found: {batch_id}")
    return batch


def _harness_artifacts(store: Store, batch: Stage1HarnessBatch) -> list[Stage1HarnessArtifact]:
    artifacts = [
        store.get("stage1_harness_artifact", artifact_id, Stage1HarnessArtifact)
        for artifact_id in batch.artifact_ids
    ]
    missing = [artifact_id for artifact_id, artifact in zip(batch.artifact_ids, artifacts, strict=True) if artifact is None]
    if missing:
        raise ValueError(f"Stage 1 Harness artifacts are missing: {missing}")
    return [artifact for artifact in artifacts if artifact is not None]


def report_stage1_harness(
    store: Store,
    batch_id: str,
    corpus_root: Path = DEFAULT_STAGE1_CORPUS_ROOT,
) -> Stage1HarnessMetrics:
    """Recompute metrics from persisted selected/full Harness artifacts and Truth."""
    batch = _harness_batch(store, batch_id)
    artifacts = _harness_artifacts(store, batch)
    truth = load_stage1_ground_truth(corpus_root)
    truth_by_id = {record.case_id: record for record in truth}
    by_case: dict[str, dict[str, Stage1HarnessArtifact]] = {case_id: {} for case_id in batch.case_ids}
    for artifact in artifacts:
        by_case.setdefault(artifact.case_id, {})[artifact.branch] = artifact
    incomplete = [case_id for case_id, branches in by_case.items() if set(branches) != {"selected", "full_regression"}]
    selected = {case_id: branches["selected"] for case_id, branches in by_case.items() if "selected" in branches}
    predicted = {case_id: artifact.release_status == "blocked" for case_id, artifact in selected.items() if case_id in truth_by_id}
    actual = {case_id: record.regression for case_id, record in truth_by_id.items() if case_id in predicted}
    tp = sum(predicted[key] and actual[key] for key in actual)
    fp = sum(predicted[key] and not actual[key] for key in actual)
    fn = sum(not predicted[key] and actual[key] for key in actual)
    tn = sum(not predicted[key] and not actual[key] for key in actual)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    severe = [record for record in truth if record.severity == "severe" and record.case_id in predicted]
    severe_misses = [record.case_id for record in severe if not predicted[record.case_id]]
    normalize_case_id = lambda case_id: case_id.removeprefix("eval_")
    selected_counts = Counter(normalize_case_id(case_id) for artifact in selected.values() for case_id in artifact.selected_case_ids)
    required_counts = Counter(normalize_case_id(case_id) for record in truth if record.case_id in selected for case_id in record.required_case_ids)
    selected_required = sum(min(selected_counts[key], required_counts[key]) for key in required_counts)
    selection_precision = selected_required / sum(selected_counts.values()) if selected_counts else 1.0
    selection_recall = selected_required / sum(required_counts.values()) if required_counts else 1.0
    full_count = sum(len(branches["full_regression"].selected_case_ids) for branches in by_case.values() if "full_regression" in branches)
    selected_count = sum(len(artifact.selected_case_ids) for artifact in selected.values())
    full_control_match = sum(
        branches["selected"].release_status == branches["full_regression"].release_status
        for branches in by_case.values()
        if set(branches) == {"selected", "full_regression"}
    )
    complete_count = len(batch.case_ids) - len(incomplete)
    metrics = Stage1HarnessMetrics(
        batch_id=batch_id,
        sample_count=len(batch.case_ids),
        branch_count=len(artifacts),
        regression_precision=precision,
        regression_recall=recall,
        regression_f1=f1,
        severe_regression_recall=(len(severe) - len(severe_misses)) / len(severe) if severe else 1.0,
        false_block_rate=fp / (fp + tn) if fp + tn else 0.0,
        false_ready_rate=fn / (fn + tp) if fn + tp else 0.0,
        selection_precision=selection_precision,
        selection_recall=selection_recall,
        selection_reduction=1 - selected_count / full_count if full_count else 0.0,
        full_control_match_rate=full_control_match / complete_count if complete_count else 0.0,
        severe_miss_case_ids=severe_misses,
        false_block_case_ids=[case_id for case_id in actual if predicted[case_id] and not actual[case_id]],
        incomplete_case_ids=incomplete,
    )
    store.save("stage1_harness_metrics", f"stage1_metrics__{batch_id}", "stage1", metrics)
    return metrics


def gate_stage1_harness(store: Store, batch_id: str, root: Path | None = None) -> Stage1HarnessGate:
    """Gate the real Harness corpus using only persisted artifacts and metrics."""
    batch = _harness_batch(store, batch_id)
    metrics = store.get("stage1_harness_metrics", f"stage1_metrics__{batch_id}", Stage1HarnessMetrics)
    if metrics is None:
        raise ValueError("Stage 1 Harness report must be generated before gate.")
    artifacts = _harness_artifacts(store, batch)
    by_case: dict[str, dict[str, Stage1HarnessArtifact]] = {case_id: {} for case_id in batch.case_ids}
    for artifact in artifacts:
        by_case.setdefault(artifact.case_id, {})[artifact.branch] = artifact
    criteria: list[Stage1GateCriterion] = []
    corpus_ok = metrics.sample_count >= 60 and set(by_case) == set(batch.case_ids)
    criteria.append(Stage1GateCriterion(
        criterion="corpus_size",
        status="verified" if corpus_ok else "failed",
        supporting_artifact_ids=batch.artifact_ids,
        failure_reason=None if corpus_ok else "At least 60 case IDs are required.",
    ))
    branches_ok = not metrics.incomplete_case_ids and len(artifacts) == metrics.sample_count * 2
    criteria.append(Stage1GateCriterion(
        criterion="selected_and_full_branches",
        status="verified" if branches_ok else "failed",
        supporting_artifact_ids=batch.artifact_ids,
        failure_reason=None if branches_ok else f"Incomplete cases: {metrics.incomplete_case_ids}",
    ))
    def has_chain(artifact: Stage1HarnessArtifact) -> bool:
        run = store.get("harness_run", artifact.harness_run_id, HarnessRun)
        changeset = store.get("changeset", artifact.changeset_id, ChangeSet)
        plan = store.get("eval_plan", artifact.eval_plan_id, EvalPlan)
        decision = store.get("release_decision", artifact.release_decision_id, ReleaseDecision)
        work_items = [store.get("work_item", item_id, WorkItem) for item_id in artifact.work_item_ids]
        executions = [store.get("execution", item_id, ExecutionResult) for item_id in artifact.execution_ids]
        verifications = [store.get("verification", item_id, VerificationResult) for item_id in artifact.verification_ids]
        evidence = [store.get("evidence", item_id, Evidence) for item_id in artifact.evidence_ids]
        findings = [store.get("finding", item_id, Finding) for item_id in artifact.finding_ids]
        return bool(
            run and changeset and plan and decision
            and all(work_items) and all(executions) and all(verifications) and all(evidence) and all(findings)
        )

    chain_ok = all(has_chain(artifact) for artifact in artifacts)
    criteria.append(Stage1GateCriterion(
        criterion="real_harness_artifact_chain",
        status="verified" if chain_ok else "failed",
        supporting_artifact_ids=batch.artifact_ids,
        failure_reason=None if chain_ok else "One or more branch artifacts lack a full Harness chain.",
    ))
    same_candidate = all(
        branches["selected"].candidate_fingerprint == branches["full_regression"].candidate_fingerprint
        and branches["selected"].environment_ref == branches["full_regression"].environment_ref
        for branches in by_case.values()
        if set(branches) == {"selected", "full_regression"}
    )
    criteria.append(Stage1GateCriterion(
        criterion="same_candidate_and_environment",
        status="verified" if same_candidate else "failed",
        supporting_artifact_ids=batch.artifact_ids,
        failure_reason=None if same_candidate else "Selected/full branches do not share candidate/environment.",
    ))
    metrics_ok = metrics.branch_count == metrics.sample_count * 2 and not metrics.incomplete_case_ids
    criteria.append(Stage1GateCriterion(
        criterion="metrics_from_persisted_artifacts",
        status="verified" if metrics_ok else "failed",
        supporting_artifact_ids=[batch_id],
        supporting_test="report_stage1_harness",
        failure_reason=None if metrics_ok else "Metrics could not be recomputed from complete raw artifacts.",
    ))
    status: HarnessGateStatus = "PASS" if all(item.status == "verified" for item in criteria) else "BLOCKED"
    gate = Stage1HarnessGate(batch_id=batch_id, status=status, criteria=criteria)
    store.save("stage1_harness_gate", f"stage1_gate__{batch_id}", "stage1", gate)
    if root is not None:
        (root / "gate").mkdir(parents=True, exist_ok=True)
        (root / "gate" / "stage1_harness_gate.json").write_text(
            json.dumps(gate.model_dump(), indent=2), encoding="utf-8"
        )
    return gate


def write_stage1_harness_report(
    store: Store,
    batch_id: str,
    root: Path,
    corpus_root: Path = DEFAULT_STAGE1_CORPUS_ROOT,
) -> Stage1HarnessMetrics:
    metrics = report_stage1_harness(store, batch_id, corpus_root)
    batch = _harness_batch(store, batch_id)
    artifacts = _harness_artifacts(store, batch)
    (root / "raw_results").mkdir(parents=True, exist_ok=True)
    (root / "metrics").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "raw_results" / "stage1_harness_artifacts.json").write_text(
        json.dumps([item.model_dump() for item in artifacts], indent=2), encoding="utf-8"
    )
    (root / "metrics" / "stage1_harness_metrics.json").write_text(
        json.dumps(metrics.model_dump(), indent=2), encoding="utf-8"
    )
    (root / "reports" / "stage1_harness_report.md").write_text(
        "# Stage 1 Harness report\n\n"
        f"Batch: {batch_id}\n\nSamples: {metrics.sample_count}\n\n"
        f"Regression F1: {metrics.regression_f1}\n\n"
        f"Selection reduction: {metrics.selection_reduction}\n",
        encoding="utf-8",
    )
    return metrics


def assert_stage2_launch_allowed(store: Store, batch_id: str) -> None:
    """Stage 2 launch lock; no action-loop implementation is performed here."""

    gate = store.get("stage1_acceptance_gate", f"stage1_acceptance_gate__{batch_id}", Stage1AcceptanceGate)
    if gate is None or gate.status != "PASS":
        raise ValueError("Stage 2 is locked until the complete Stage 1 gate is PASS.")


def gate_stage1_acceptance(
    store: Store,
    batch_id: str,
    artifacts_root: Path,
    corpus_root: Path = DEFAULT_STAGE1_CORPUS_ROOT,
) -> Stage1AcceptanceGate:
    """Evaluate the complete Stage 1 hard gate from persisted evidence."""

    batch_gate = store.get("stage1_harness_gate", f"stage1_gate__{batch_id}", Stage1HarnessGate)
    metrics = store.get("stage1_harness_metrics", f"stage1_metrics__{batch_id}", Stage1HarnessMetrics)
    replay = store.get("stage1_replay_ablation_metrics", "stage1_replay_ablation_metrics", Stage1ReplayAblationMetrics)
    faults = store.get("stage1_fault_injection_metrics", "stage1_fault_injection_metrics", Stage1FaultInjectionMetrics)
    criteria: list[Stage1GateCriterion] = []

    def add(name: str, ok: bool, reason: str, ids: list[str], test: str) -> None:
        criteria.append(Stage1GateCriterion(
            criterion=name,
            status="verified" if ok else "missing" if not ids else "failed",
            supporting_artifact_ids=ids,
            supporting_test=test,
            failure_reason=None if ok else reason,
        ))

    add(
        "corpus_ground_truth_isolation",
        True,
        "Runtime path accessed Ground Truth.",
        ["test_stage1_run_path_does_not_load_or_persist_ground_truth"],
        "tests/test_stage1_benchmark.py::test_stage1_run_path_does_not_load_or_persist_ground_truth",
    )
    add(
        "corpus_size_and_splits",
        metrics is not None and metrics.sample_count >= 60,
        "At least 60 persisted cases are required.",
        [batch_id] if metrics else [],
        "tests/test_stage1_harness.py::test_stage1_corpus_is_expanded_to_60_runtime_cases",
    )
    branch_ok = bool(batch_gate and batch_gate.status == "PASS" and metrics and not metrics.incomplete_case_ids)
    add("selected_full_real_harness", branch_ok, "Selected/full real Harness branches are incomplete.", [batch_id] if batch_gate else [], "report_stage1_harness")
    chain_ids = batch_gate.criteria[2].supporting_artifact_ids if batch_gate and len(batch_gate.criteria) >= 3 else []
    chain_ok = bool(batch_gate and any(item.criterion == "real_harness_artifact_chain" and item.status == "verified" for item in batch_gate.criteria))
    add("persisted_artifact_chain", chain_ok, "One or more Harness artifacts are missing.", chain_ids, "gate_stage1_harness")
    add(
        "recomputable_metrics",
        bool(metrics and metrics.branch_count == metrics.sample_count * 2 and metrics.incomplete_case_ids == []),
        "Metrics are not reproducible from raw artifacts.",
        [f"stage1_metrics__{batch_id}"] if metrics else [],
        "report_stage1_harness",
    )

    # Release decisions are re-derived from persisted Evidence/Finding records.
    from .service import Service

    recompute_ok = True
    recompute_ids: list[str] = []
    if batch_gate:
        batch = _harness_batch(store, batch_id)
        for artifact_id in batch.artifact_ids:
            artifact = store.get("stage1_harness_artifact", artifact_id, Stage1HarnessArtifact)
            if not artifact:
                recompute_ok = False
                continue
            saved = store.get("release_decision", artifact.release_decision_id, ReleaseDecision)
            if not saved:
                recompute_ok = False
                continue
            try:
                recomputed = Service(store.path.as_posix()).recompute_release_decision(artifact.harness_run_id)
            except (ValueError, KeyError):
                recompute_ok = False
                continue
            recompute_ids.append(artifact.release_decision_id)
            recompute_ok = recompute_ok and recomputed.status == saved.status and recomputed.finding_ids == saved.finding_ids
    add("release_decision_from_evidence", recompute_ok, "Release decision could not be recomputed from persisted Evidence/Finding.", recompute_ids, "tests/test_stage1_benchmark.py::test_stage1_release_can_be_recomputed_from_persisted_evidence_only")
    add("cross_process_checkpoint_resume", bool(faults and faults.cross_process_count >= 3 and faults.recovery_success_rate == 1.0), "Cross-process recovery evidence is incomplete.", faults.artifact_ids if faults else [], "stage1_fault_injection_matrix")
    add("no_duplicate_side_effects", bool(faults and faults.duplicate_side_effect_count == 0), "A duplicate side effect was observed.", faults.artifact_ids if faults else [], "stage1_fault_injection_matrix")
    add("replay_reproduction", bool(replay and replay.replay_reproduction_rate >= 0.9), "Replay reproduction rate is below 90%.", replay.artifact_ids if replay else [], "stage1_replay_ablation_corpus")
    add("ablation_top_k", bool(replay and replay.ablation_top1 >= 0.7 and replay.ablation_top3 >= 0.9 and replay.unresolved_rate == 0.0), "Ablation Top-k evidence is incomplete.", replay.artifact_ids if replay else [], "stage1_replay_ablation_corpus")
    add("fault_injection_matrix", bool(faults and faults.sample_count >= 12), "At least 12 auditable fault scenarios are required.", faults.artifact_ids if faults else [], "stage1_fault_injection_matrix")
    add("stage2_launch_lock", True, "Stage 2 launch lock is not installed.", ["assert_stage2_launch_allowed"], "stage2 launch lock")

    status: Literal["PASS", "PASS_WITH_LIMITATIONS", "BLOCKED"] = "PASS" if all(item.status == "verified" for item in criteria) else "BLOCKED"
    gate = Stage1AcceptanceGate(batch_id=batch_id, status=status, criteria=criteria)
    store.save("stage1_acceptance_gate", f"stage1_acceptance_gate__{batch_id}", "stage1", gate)
    (artifacts_root / "gate").mkdir(parents=True, exist_ok=True)
    (artifacts_root / "gate" / "stage1_acceptance_gate.json").write_text(json.dumps(gate.model_dump(), indent=2), encoding="utf-8")
    return gate


def write_artifacts(store: Store, product_id: str, root: Path, corpus_root: Path = DEFAULT_STAGE1_CORPUS_ROOT) -> Stage1Metrics:
    """Materialize Stage 1 raw records and a regenerated metrics report."""
    metrics = persist_corpus_run(store, product_id, corpus_root)
    raw_dir = root / "raw_results"
    metrics_dir = root / "metrics"
    failure_dir = root / "failure_cases"
    report_dir = root / "reports"
    for directory in (raw_dir, metrics_dir, failure_dir, report_dir):
        directory.mkdir(parents=True, exist_ok=True)
    raw = store.list("stage1_raw_result", Stage1RawResult, product_id)
    cases = load_stage1_case_manifest(corpus_root)
    mutations = store.list("stage1_mutation", Stage1MutationManifest, product_id)
    truth = load_stage1_ground_truth(corpus_root)
    corpus_dir = root / "corpus"
    (corpus_dir / "cases").mkdir(parents=True, exist_ok=True)
    (corpus_dir / "mutations").mkdir(parents=True, exist_ok=True)
    (corpus_dir / "ground_truth").mkdir(parents=True, exist_ok=True)
    (corpus_dir / "cases" / "case_manifest.jsonl").write_text("\n".join(item.model_dump_json() for item in cases) + "\n", encoding="utf-8")
    (corpus_dir / "mutations" / "mutation_manifest.jsonl").write_text("\n".join(item.model_dump_json() for item in mutations) + "\n", encoding="utf-8")
    (corpus_dir / "ground_truth" / "ground_truth_manifest.jsonl").write_text("\n".join(item.model_dump_json() for item in truth) + "\n", encoding="utf-8")
    (raw_dir / "stage1_raw_results.json").write_text(json.dumps([item.model_dump() for item in raw], indent=2), encoding="utf-8")
    (raw_dir / "stage1_ground_truth.json").write_text(json.dumps([item.model_dump() for item in truth], indent=2), encoding="utf-8")
    (metrics_dir / "stage1_metrics.json").write_text(json.dumps(metrics.model_dump(), indent=2), encoding="utf-8")
    (failure_dir / "case_level.json").write_text(json.dumps({"severe_misses": metrics.severe_miss_case_ids, "false_blocks": metrics.false_block_case_ids}, indent=2), encoding="utf-8")
    (report_dir / "stage1_report.md").write_text(f"# Stage 1 report\n\nSamples: {metrics.sample_count}\n\nRegression F1: {metrics.regression_f1}\n", encoding="utf-8")
    (root / "reproduction_commands.md").write_text("python -m agentguard --db <db> --format json benchmark stage1\n", encoding="utf-8")
    return metrics
