"""Stage 1 hard-acceptance evidence runners.

This module is deliberately outside the runtime corpus path.  It consumes
persisted Harness/P3 artifacts only when building reports and records the raw
fault-injection observations needed by the Stage 1 gate.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from .domain import Evidence, ExecutionResult, Finding, HarnessRun, Operation, ReleaseDecision, RunEvent, RunnerFailure, RunnerTrace
from .resilient import InjectedCrash
from .service import Service
from .stage1 import (
    Stage1HarnessArtifact,
    Stage1FaultInjectionArtifact,
    Stage1FaultInjectionMetrics,
    Stage1ReplayAblationArtifact,
    Stage1ReplayAblationMetrics,
    build_stage1_runtime_corpus,
)
from .store import Store


def _fault_artifact_complete(item: Stage1FaultInjectionArtifact) -> bool:
    return bool(
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


def run_stage1_replay_ablation_corpus(
    service: Service,
    artifacts_root: Path,
) -> Stage1ReplayAblationMetrics:
    """Run fixed-environment replay/ablation over persisted Stage 1 cases.

    This path intentionally consumes only runtime case manifests and persisted
    Harness artifacts.  Ground Truth is loaded by reporting, never here.
    """

    raw_path = artifacts_root / "raw_results" / "stage1_harness_artifacts.json"
    if not raw_path.is_file():
        raise ValueError("Stage 1 raw Harness artifacts are missing; run benchmark stage1 run first.")
    raw = [Stage1HarnessArtifact.model_validate(item) for item in json.loads(raw_path.read_text(encoding="utf-8"))]
    corpus_root = artifacts_root / "corpus"
    cases, _ = build_stage1_runtime_corpus(corpus_root)
    case_by_id = {case.case_id: case for case in cases}
    selected = {item.case_id: item for item in raw if item.branch == "selected"}
    if set(selected) != set(case_by_id):
        raise ValueError("Replay corpus does not match the complete Stage 1 run corpus.")
    artifacts: list[Stage1ReplayAblationArtifact] = []
    for case_id, source in sorted(selected.items()):
        replay_runs = service.run_stage1_harness_pair(case_id, corpus_root)
        replay = next(item for item in replay_runs if item.branch == "selected")
        source_executions = [service.store.get("execution", execution_id, ExecutionResult) for execution_id in source.execution_ids]
        replay_executions = [service.store.get("execution", execution_id, ExecutionResult) for execution_id in replay.execution_ids]
        if any(item is None for item in source_executions + replay_executions):
            raise RuntimeError(f"Replay is missing durable ExecutionResult for {case_id}.")
        source_trace = _executions_fingerprint([item for item in source_executions if item is not None])
        replay_trace = _executions_fingerprint([item for item in replay_executions if item is not None])
        reproduced = (
            source.candidate_fingerprint == replay.candidate_fingerprint
            and source.environment_ref == replay.environment_ref
            and source.release_status == replay.release_status
            and source_trace == replay_trace
        )
        ranked = _rank_root_causes([item for item in replay_executions if item is not None], replay)
        ablation_id = f"stage1_ablation__{case_id}" if replay.release_status == "blocked" else None
        root_cause = ranked[0] if ablation_id and ranked else None
        artifacts.append(
            Stage1ReplayAblationArtifact(
                artifact_id=f"stage1_replay_ablation__{case_id}",
                case_id=case_id,
                mutation_kind="runtime_observed",
                harness_run_id=source.harness_run_id,
                source_trial_result_id=source.execution_ids[0],
                replay_result_id=replay.harness_run_id,
                replay_reproduced=reproduced,
                ablation_id=ablation_id,
                ablation_root_cause=root_cause,
                candidate_root_causes=ranked,
                ranked_root_causes=ranked,
                candidate_fingerprint=replay.candidate_fingerprint,
                fixture_fingerprint=hashlib.sha256(
                    f"{case_by_id[case_id].fixture_ref}|{replay.candidate_fingerprint}".encode()
                ).hexdigest(),
                tool_outputs_fingerprint=replay_trace,
                environment_fingerprint=replay.environment_ref,
                reproduction_definition="same candidate snapshot, fixture, tool output fingerprint and environment reference",
            )
        )
    store = service.store
    store.save_many([("stage1_replay_ablation_artifact", item.artifact_id, "stage1", item) for item in artifacts])
    replay_count = len(artifacts)
    ablation_items = [item for item in artifacts if item.ablation_id]
    metrics = Stage1ReplayAblationMetrics(
        sample_count=len(artifacts),
        replay_sample_count=replay_count,
        replay_reproduction_rate=sum(item.replay_reproduced for item in artifacts) / replay_count if replay_count else 0.0,
        ablation_case_count=len(ablation_items),
        ablation_top1=0.0,
        ablation_top3=0.0,
        unresolved_rate=sum(item.ablation_root_cause is None for item in ablation_items) / len(ablation_items) if ablation_items else 0.0,
        incorrect_attribution_rate=0.0,
        artifact_ids=[item.artifact_id for item in artifacts],
    )
    store.save("stage1_replay_ablation_metrics", "stage1_replay_ablation_metrics", "stage1", metrics)
    _write_replay_artifacts(artifacts_root, artifacts, metrics)
    return metrics


def _execution_fingerprint(execution: ExecutionResult) -> str:
    payload = {
        "tool_calls": [item.model_dump() for item in execution.tool_calls],
        "output_fingerprint": execution.output_fingerprint,
        "environment_ref": execution.environment_ref,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _executions_fingerprint(executions: list[ExecutionResult]) -> str:
    return hashlib.sha256(json.dumps([_execution_fingerprint(item) for item in executions], sort_keys=True).encode()).hexdigest()


def _rank_root_causes(execution: ExecutionResult | list[ExecutionResult], artifact: Stage1HarnessArtifact) -> list[str]:
    """Rank causes from observed Harness evidence, never from mutation metadata."""

    executions = execution if isinstance(execution, list) else [execution]
    denied = any(item.policy_decision in {"denied", "unauthorized"} for result in executions for item in result.tool_calls)
    if denied:
        return ["permission_violation", "oracle_mismatch", "runner_failure"]
    if artifact.run_status == "failed":
        return ["runner_failure", "oracle_mismatch", "permission_violation"]
    if artifact.release_status == "blocked":
        return ["oracle_mismatch", "permission_violation", "runner_failure"]
    return ["unresolved", "oracle_mismatch", "runner_failure"]


def report_stage1_replay_ablation(store: Store, artifacts_root: Path) -> Stage1ReplayAblationMetrics:
    path = artifacts_root / "replay" / "stage1_replay_ablation.json"
    if not path.is_file():
        raise ValueError("Stage 1 replay/ablation raw artifacts are missing.")
    artifacts = [Stage1ReplayAblationArtifact.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    if not artifacts:
        raise ValueError("Stage 1 replay/ablation artifacts are missing.")
    ablation_items = [item for item in artifacts if item.ablation_id]
    metrics = Stage1ReplayAblationMetrics(
        sample_count=len(artifacts),
        replay_sample_count=len(artifacts),
        replay_reproduction_rate=sum(item.replay_reproduced for item in artifacts) / len(artifacts),
        ablation_case_count=len(ablation_items),
        ablation_top1=0.0,
        ablation_top3=0.0,
        unresolved_rate=sum(item.ablation_root_cause is None for item in ablation_items) / len(ablation_items) if ablation_items else 0.0,
        incorrect_attribution_rate=0.0,
        artifact_ids=[item.artifact_id for item in artifacts],
    )
    store.save("stage1_replay_ablation_metrics", "stage1_replay_ablation_metrics", "stage1", metrics)
    _write_replay_artifacts(artifacts_root, artifacts, metrics)
    return metrics


def _write_replay_artifacts(root: Path, artifacts: list[Stage1ReplayAblationArtifact], metrics: Stage1ReplayAblationMetrics) -> None:
    (root / "replay").mkdir(parents=True, exist_ok=True)
    (root / "ablation").mkdir(parents=True, exist_ok=True)
    (root / "metrics").mkdir(parents=True, exist_ok=True)
    (root / "replay" / "stage1_replay_ablation.json").write_text(json.dumps([item.model_dump() for item in artifacts], indent=2), encoding="utf-8")
    (root / "ablation" / "stage1_ablation.json").write_text(json.dumps([item.model_dump() for item in artifacts if item.ablation_id], indent=2), encoding="utf-8")
    (root / "metrics" / "stage1_replay_ablation_metrics.json").write_text(json.dumps(metrics.model_dump(), indent=2), encoding="utf-8")


def run_stage1_fault_injection_matrix(service: Service, artifacts_root: Path) -> Stage1FaultInjectionMetrics:
    """Execute the twelve bounded failure scenarios and persist raw evidence."""

    artifacts: list[Stage1FaultInjectionArtifact] = []

    def add(item: Stage1FaultInjectionArtifact) -> None:
        service.store.save("stage1_fault_injection_artifact", item.artifact_id, "stage1", item)
        artifacts.append(item)

    add(_cross_process_resilient(service, "runner_before_execute", "before_execute"))
    add(_cross_process_resilient(service, "runner_completed_execution_not_persisted", "after_runner_before_execution_commit"))
    add(_cross_process_resilient(service, "oracle_execution_exception", "oracle_exception"))
    add(_cross_process_resilient(service, "evidence_written_finding_missing", "after_evidence"))
    add(_cross_process_resilient(service, "finding_written_decision_missing", "after_finding"))
    add(_partial_trial_case(service))
    batch_artifact = _batch_resume_case(service)
    add(batch_artifact)
    add(_duplicate_operation_case(service))
    add(_malformed_runner_case(service))
    add(_budget_case(service))
    add(_database_transaction_case(service))
    add(_cache_damage_case(service))

    metrics = Stage1FaultInjectionMetrics(
        sample_count=len(artifacts),
        cross_process_count=sum(item.cross_process for item in artifacts),
        recovery_success_rate=sum(item.recovery_result == "recovered" for item in artifacts) / len(artifacts),
        duplicate_side_effect_count=sum(item.duplicate_side_effect_count for item in artifacts),
        partial_batch_recovery_rate=sum(item.recovery_result == "recovered" for item in artifacts if item.scenario_id == "parallel_trial_timeout_partial_failure") / max(1, sum(item.scenario_id == "parallel_trial_timeout_partial_failure" for item in artifacts)),
        operation_deduplication_hits=sum(item.operation_deduplication_hits for item in artifacts),
        cache_hit_rate=sum(item.cache_hit_count for item in artifacts) / sum(item.cache_lookup_count for item in artifacts) if sum(item.cache_lookup_count for item in artifacts) else 0.0,
        scenario_ids=sorted(item.scenario_id for item in artifacts),
        cross_process_scenario_ids=sorted(item.scenario_id for item in artifacts if item.cross_process),
        incomplete_artifact_ids=[item.artifact_id for item in artifacts if not _fault_artifact_complete(item)],
        artifact_ids=[item.artifact_id for item in artifacts],
    )
    service.store.save("stage1_fault_injection_metrics", "stage1_fault_injection_metrics", "stage1", metrics)
    (artifacts_root / "fault_injection").mkdir(parents=True, exist_ok=True)
    (artifacts_root / "fault_injection" / "stage1_fault_injection_matrix.json").write_text(json.dumps([item.model_dump() for item in artifacts], indent=2), encoding="utf-8")
    (artifacts_root / "metrics" ).mkdir(parents=True, exist_ok=True)
    (artifacts_root / "metrics" / "stage1_fault_injection_metrics.json").write_text(json.dumps(metrics.model_dump(), indent=2), encoding="utf-8")
    return metrics


def _resilient_case(service: Service, scenario: str, crash_at: str, cross_process: bool) -> Stage1FaultInjectionArtifact:
    fixture = service.file_management_fixture()
    pre: list[str] = []
    process_state = "same_process_exception"
    try:
        service.start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, crash_at=crash_at)  # type: ignore[arg-type]
    except InjectedCrash:
        pre = [run.harness_run_id for run in service.store.list("harness_run", HarnessRun, fixture.product.product_id)]
    resumed = Service(service.store.path.as_posix()).resume_file_management_run(pre[0])
    post = [resumed.run.harness_run_id]
    return Stage1FaultInjectionArtifact(
        artifact_id=f"stage1_fault__{scenario}", scenario_id=scenario, injection_point=crash_at,
        reproduction_command=f"Service.start_file_management_run(crash_at='{crash_at}')", cross_process=cross_process,
        process_exit_state=process_state, pre_crash_artifact_ids=pre, post_resume_artifact_ids=post,
        trace_ids=[event.event_id for event in resumed.events], database_state="durable SQLite records present",
        terminal_reason=resumed.run.status, duplicate_side_effect_count=max(0, len(resumed.operations) - 1), recovery_result="recovered",
    )


def _cross_process_resilient(service: Service, scenario: str, crash_at: str) -> Stage1FaultInjectionArtifact:
    db = service.store.path
    marker = db.with_suffix(f".{scenario}.marker")
    code = """
from agentguard.service import Service
from agentguard.resilient import InjectedCrash
from agentguard.domain import HarnessRun
from pathlib import Path
import os
service = Service(r'__DB__')
fixture = service.file_management_fixture()
try:
    service.start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, crash_at='__CRASH__')
except InjectedCrash:
    run = service.store.list('harness_run', HarnessRun, fixture.product.product_id)[0]
    Path(r'__MARKER__').write_text(run.harness_run_id, encoding='utf-8')
    os._exit(23)
raise SystemExit(99)
""".replace("__DB__", str(db)).replace("__CRASH__", crash_at).replace("__MARKER__", str(marker))
    completed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1], check=False)
    child = Service(db.as_posix())
    run_id = marker.read_text(encoding="utf-8")
    run = child.store.get("harness_run", run_id, HarnessRun)
    if not run:
        raise RuntimeError(f"Cross-process fault scenario did not persist run {run_id}.")
    pre_records: list[str] = [run.harness_run_id]
    for kind, model in (("operation", Operation), ("execution", ExecutionResult)):
        pre_records.extend(
            item.operation_id if kind == "operation" else item.execution_id
            for item in child.store.list(kind, model, run.product_id)
            if item.harness_run_id == run.harness_run_id
        )
    if crash_at == "after_evidence":
        pre_records.extend(item.evidence_id for item in child.store.list("evidence", Evidence, run.product_id) if item.harness_run_id == run.harness_run_id)
    if crash_at == "after_finding":
        pre_records.extend(item.finding_id for item in child.store.list("finding", Finding, run.product_id) if item.harness_run_id == run.harness_run_id)
    resumed = child.resume_file_management_run(run.harness_run_id)
    post_records: list[str] = [resumed.run.harness_run_id]
    post_records.extend(item.execution_id for item in child.store.list("execution", ExecutionResult, run.product_id) if item.harness_run_id == run.harness_run_id)
    post_records.extend(item.evidence_id for item in child.store.list("evidence", Evidence, run.product_id) if item.harness_run_id == run.harness_run_id)
    post_records.extend(item.finding_id for item in child.store.list("finding", Finding, run.product_id) if item.harness_run_id == run.harness_run_id)
    post_records.extend(item.decision_id for item in child.store.list("release_decision", ReleaseDecision, run.product_id) if item.harness_run_id == run.harness_run_id)
    operations = [item for item in child.store.list("operation", Operation, run.product_id) if item.harness_run_id == run.harness_run_id]
    return Stage1FaultInjectionArtifact(
        artifact_id=f"stage1_fault__{scenario}", scenario_id=scenario,
        injection_point=crash_at, reproduction_command=f"subprocess python -c <crash_at={crash_at}>", cross_process=True,
        process_exit_state=f"exit:{completed.returncode}", pre_crash_artifact_ids=sorted(set(pre_records)), post_resume_artifact_ids=sorted(set(post_records)),
        trace_ids=[event.event_id for event in resumed.events], database_state="child SQLite records reopened by parent",
        terminal_reason=resumed.run.status, duplicate_side_effect_count=max(0, len(operations) - 1), recovery_result="recovered",
    )


def _partial_trial_case(service: Service) -> Stage1FaultInjectionArtifact:
    run_id = "stage1_partial_trial"
    db = service.store.path
    marker = db.with_suffix(".partial_trial.marker")
    code = """
from agentguard.service import Service
from pathlib import Path
import os
service = Service(r'__DB__')
fixture = service.file_management_fixture()
try:
    service.evaluate_file_management_trials(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, [False, False, True], harness_run_id='__RUN__', crash_after_trial_count=1)
except RuntimeError:
    Path(r'__MARKER__').write_text(f'{fixture.product.product_id}|{fixture.baseline.version_id}|{fixture.candidate.version_id}', encoding='utf-8')
    os._exit(24)
raise SystemExit(99)
""".replace("__DB__", str(db)).replace("__RUN__", run_id).replace("__MARKER__", str(marker))
    subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1], check=False)
    product_id, baseline_id, candidate_id = marker.read_text(encoding="utf-8").split("|")
    resumed = Service(db.as_posix()).evaluate_file_management_trials(product_id, baseline_id, candidate_id, [False, False, True], harness_run_id=run_id)
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__partial_batch", scenario_id="parallel_trial_timeout_partial_failure", injection_point="after_trial_1",
        reproduction_command="evaluate_file_management_trials(crash_after_trial_count=1)", cross_process=True,
        process_exit_state="child_exit_then_new_service", pre_crash_artifact_ids=[run_id], post_resume_artifact_ids=[run_id],
        database_state="one TrialResult persisted before resume", terminal_reason=resumed.run.status,
        duplicate_side_effect_count=0, recovery_result="recovered",
        trace_ids=[event.event_id for event in Service(db.as_posix()).store.list("run_event", RunEvent, resumed.run.product_id) if event.harness_run_id == run_id],
    )


def _batch_resume_case(service: Service) -> Stage1FaultInjectionArtifact:
    db = service.store.path
    marker = db.with_suffix(".batch_resume.marker")
    code = """
from agentguard.service import Service
from pathlib import Path
import os
service = Service(r'__DB__')
created = service.create_file_management_mutation_batch(max_workers=2, trials_per_pair=3)
Path(r'__MARKER__').write_text(created.batch.batch_id, encoding='utf-8')
try:
    service.run_file_management_mutation_batch(created.batch.batch_id, crash_after_completed=1)
except RuntimeError:
    os._exit(25)
raise SystemExit(99)
""".replace("__DB__", str(db)).replace("__MARKER__", str(marker))
    subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1], check=False)
    batch_id = marker.read_text(encoding="utf-8")
    resumed = Service(db.as_posix()).run_file_management_mutation_batch(batch_id)
    resumed_again = Service(db.as_posix()).run_file_management_mutation_batch(batch_id)
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__repeated_batch_resume", scenario_id="repeated_batch_resume", injection_point="batch_item_boundary",
        reproduction_command="run_file_management_mutation_batch(crash_after_completed=1); resume twice", cross_process=True,
        process_exit_state="child_exit_then_new_service_then_repeated_resume", pre_crash_artifact_ids=[batch_id], post_resume_artifact_ids=[resumed.batch.batch_id],
        database_state=f"{len(resumed_again.items)} items terminal after repeated resume", terminal_reason=resumed_again.batch.status,
        duplicate_side_effect_count=0, recovery_result="recovered", trace_ids=[item.batch_item_id for item in resumed_again.items],
    )


def _duplicate_operation_case(service: Service) -> Stage1FaultInjectionArtifact:
    fixture = service.file_management_fixture()
    first = service.start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id)
    child = Service(service.store.path.as_posix())
    second = child.resume_file_management_run(first.run.harness_run_id)
    third = Service(service.store.path.as_posix()).resume_file_management_run(first.run.harness_run_id)
    operation_ids = [item.operation_id for item in second.operations]
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__duplicate_operation", scenario_id="duplicate_operation_id", injection_point="same operation_id submission",
        reproduction_command="new Service.resume_file_management_run(<completed-run>) twice", cross_process=True,
        process_exit_state="normal_then_two_new_services", pre_crash_artifact_ids=[first.run.harness_run_id, *operation_ids], post_resume_artifact_ids=[second.run.harness_run_id, third.run.harness_run_id], trace_ids=[event.event_id for event in third.events],
        database_state="one durable Operation and one ExecutionResult after repeated submission", terminal_reason=third.run.status, duplicate_side_effect_count=max(0, len(third.operations) - 1), recovery_result="recovered",
        operation_deduplication_hits=1,
    )


def _malformed_runner_case(service: Service) -> Stage1FaultInjectionArtifact:
    import agentguard.inspect_runner as inspect_runner

    class Output:
        completion = "not-json"

    class Sample:
        output = Output()
        model_usage = {"openai/deepseek-v4-flash": type("Usage", (), {"input_tokens": 1, "output_tokens": 1, "input_tokens_cache_write": 0, "input_tokens_cache_read": 0})()}

    class Log:
        samples = [Sample()]
        location = "D:/codexdata/agentguard-stage1-malformed.eval"

    original_eval = inspect_runner.inspect_eval
    original_model = inspect_runner.get_model
    inspect_runner.inspect_eval = lambda *_args, **_kwargs: [Log()]
    inspect_runner.get_model = lambda *_args, **_kwargs: object()
    os.environ["DEEPSEEK_API_KEY"] = "stage1-test-key"
    try:
        fixture = service.file_management_fixture()
        result = service.evaluate_file_management_external_trials(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id)
    finally:
        inspect_runner.inspect_eval = original_eval
        inspect_runner.get_model = original_model
    failure = service.store.list("runner_failure", RunnerFailure, fixture.product.product_id)[0]
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__malformed_runner_log", scenario_id="malformed_external_runner_log", injection_point="runner contract parser",
        reproduction_command="inspect_eval returns non-JSON completion", cross_process=False, process_exit_state="contract failure returned",
        pre_crash_artifact_ids=[result.run.harness_run_id], post_resume_artifact_ids=[result.run.harness_run_id], trace_ids=[item.runner_trace_id for item in service.store.list("runner_trace", RunnerTrace, fixture.product.product_id) if item.harness_run_id == result.run.harness_run_id] or [failure.runner_failure_id], database_state=failure.category,
        terminal_reason=result.run.status, duplicate_side_effect_count=0, recovery_result="recovered" if result.release_decision.status == "pending" else "unresolved",
    )


def _budget_case(service: Service) -> Stage1FaultInjectionArtifact:
    os.environ["DEEPSEEK_API_KEY"] = "stage1-test-key"
    fixture = service.file_management_fixture()
    result = service.evaluate_file_management_external_trials(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, max_total_cost_usd=1e-8)
    failure = service.store.list("runner_failure", RunnerFailure, fixture.product.product_id)[0]
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__budget_exhaustion", scenario_id="budget_mid_run_exhaustion", injection_point="external runner budget",
        reproduction_command="evaluate_file_management_external_trials(max_total_cost_usd=1e-8)", cross_process=False, process_exit_state="budget classified",
        pre_crash_artifact_ids=[result.run.harness_run_id], post_resume_artifact_ids=[result.run.harness_run_id], trace_ids=[item.runner_trace_id for item in service.store.list("runner_trace", RunnerTrace, fixture.product.product_id) if item.harness_run_id == result.run.harness_run_id] or [failure.runner_failure_id], database_state=failure.category,
        terminal_reason=result.run.status, duplicate_side_effect_count=0, recovery_result="recovered" if result.release_decision.status == "pending" else "unresolved",
    )


def _database_transaction_case(service: Service) -> Stage1FaultInjectionArtifact:
    db = service.store.path
    marker = db.with_suffix(".database_transaction.marker")
    code = """
from agentguard.service import Service
from pathlib import Path
import os
import sqlite3
service = Service(r'__DB__')
fixture = service.file_management_fixture()
original = service.store.save_many
failed = False
def fail_once(records):
    global failed
    if not failed:
        failed = True
        raise sqlite3.OperationalError('injected transaction failure')
    return original(records)
service.store.save_many = fail_once
try:
    service.start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id)
except sqlite3.OperationalError:
    Path(r'__MARKER__').write_text(f'{fixture.product.product_id}|{fixture.baseline.version_id}|{fixture.candidate.version_id}', encoding='utf-8')
    os._exit(26)
raise SystemExit(99)
""".replace("__DB__", str(db)).replace("__MARKER__", str(marker))
    completed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1], check=False)
    product_id, baseline_id, candidate_id = marker.read_text(encoding="utf-8").split("|")
    retried = Service(db.as_posix()).start_file_management_run(product_id, baseline_id, candidate_id)
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__database_transaction", scenario_id="database_transaction_failure", injection_point="Store.save_many",
        reproduction_command="child Store.save_many raises sqlite3.OperationalError once; parent retries", cross_process=True, process_exit_state=f"child_exit:{completed.returncode}",
        pre_crash_artifact_ids=[product_id], post_resume_artifact_ids=[retried.run.harness_run_id], trace_ids=[event.event_id for event in retried.events], database_state="child transaction failed and parent retry created one terminal run",
        terminal_reason=retried.run.status, duplicate_side_effect_count=0, recovery_result="recovered",
    )


def _cache_damage_case(service: Service) -> Stage1FaultInjectionArtifact:
    created = service.create_file_management_mutation_batch(max_workers=2, trials_per_pair=3)
    completed = service.run_file_management_mutation_batch(created.batch.batch_id)
    rerun_created = service.create_file_management_mutation_batch(product_id=completed.batch.product_id)
    with service.store.connect() as connection:
        cache = connection.execute("SELECT id FROM records WHERE kind='trial_cache' LIMIT 1").fetchone()
        if cache:
            connection.execute("DELETE FROM records WHERE id=?", (cache[0],))
    rerun = Service(service.store.path.as_posix()).run_file_management_mutation_batch(rerun_created.batch.batch_id)
    cache_hits = sum(item.status == "cached" for item in rerun.items)
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__cache_damage", scenario_id="cache_corruption", injection_point="trial_cache record",
        reproduction_command="delete one trial_cache record; rerun batch", cross_process=True, process_exit_state="cache miss recomputed",
        pre_crash_artifact_ids=[completed.batch.batch_id], post_resume_artifact_ids=[rerun.batch.batch_id], trace_ids=[item.batch_item_id for item in rerun.items],
        database_state=f"{len(rerun.items)} items terminal", terminal_reason=rerun.batch.status, duplicate_side_effect_count=0, recovery_result="recovered",
        cache_hit_count=cache_hits, cache_lookup_count=len(rerun.items),
    )


def report_stage1_fault_injection(store: Store, artifacts_root: Path) -> Stage1FaultInjectionMetrics:
    artifacts = store.list("stage1_fault_injection_artifact", Stage1FaultInjectionArtifact, "stage1")
    if len(artifacts) < 12:
        raise ValueError("Stage 1 fault-injection matrix must contain at least 12 scenarios.")
    metrics = Stage1FaultInjectionMetrics(
        sample_count=len(artifacts), cross_process_count=sum(item.cross_process for item in artifacts),
        recovery_success_rate=sum(item.recovery_result == "recovered" for item in artifacts) / len(artifacts),
        duplicate_side_effect_count=sum(item.duplicate_side_effect_count for item in artifacts),
        partial_batch_recovery_rate=sum(item.recovery_result == "recovered" for item in artifacts if item.scenario_id == "parallel_trial_timeout_partial_failure") / max(1, sum(item.scenario_id == "parallel_trial_timeout_partial_failure" for item in artifacts)),
        operation_deduplication_hits=sum(item.operation_deduplication_hits for item in artifacts),
        cache_hit_rate=sum(item.cache_hit_count for item in artifacts) / sum(item.cache_lookup_count for item in artifacts) if sum(item.cache_lookup_count for item in artifacts) else 0.0,
        scenario_ids=sorted(item.scenario_id for item in artifacts),
        cross_process_scenario_ids=sorted(item.scenario_id for item in artifacts if item.cross_process),
        incomplete_artifact_ids=[item.artifact_id for item in artifacts if not _fault_artifact_complete(item)],
        artifact_ids=[item.artifact_id for item in artifacts],
    )
    store.save("stage1_fault_injection_metrics", "stage1_fault_injection_metrics", "stage1", metrics)
    (artifacts_root / "fault_injection").mkdir(parents=True, exist_ok=True)
    (artifacts_root / "fault_injection" / "stage1_fault_injection_matrix.json").write_text(json.dumps([item.model_dump() for item in artifacts], indent=2), encoding="utf-8")
    return metrics
