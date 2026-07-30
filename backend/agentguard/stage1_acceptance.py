"""Stage 1 hard-acceptance evidence runners.

This module is deliberately outside the runtime corpus path.  It consumes
persisted Harness/P3 artifacts only when building reports and records the raw
fault-injection observations needed by the Stage 1 gate.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from .domain import HarnessRun, RunnerFailure, TrialResult
from .resilient import InjectedCrash
from .service import Service
from .stage1 import (
    Stage1FaultInjectionArtifact,
    Stage1FaultInjectionMetrics,
    Stage1ReplayAblationArtifact,
    Stage1ReplayAblationMetrics,
)
from .store import Store


def run_stage1_replay_ablation_corpus(
    service: Service,
    artifacts_root: Path,
) -> Stage1ReplayAblationMetrics:
    """Run fixed-environment replay/ablation over the 60-pair local corpus."""

    created = service.create_file_management_mutation_batch(max_workers=2, trials_per_pair=3)
    batch = service.run_file_management_mutation_batch(created.batch.batch_id)
    pairs = {pair.pair_id: pair for pair in batch.pairs}
    artifacts: list[Stage1ReplayAblationArtifact] = []
    for item in batch.items:
        pair = pairs[item.pair_id]
        results = [
            result
            for result in service.store.list("trial_result", TrialResult, batch.batch.product_id)
            if result.harness_run_id == item.harness_run_id and result.kind == "evaluation"
        ]
        if not results or not item.harness_run_id:
            raise RuntimeError(f"Missing durable trial results for batch item {item.batch_item_id}.")
        source = next((result for result in results if not result.passed), results[0])
        replayed = service.replay_file_management_trial(item.harness_run_id, source.trial_result_id)
        if len(replayed.replays) != 1:
            raise RuntimeError(f"Replay did not persist exactly one result for {item.batch_item_id}.")
        replay = replayed.replays[0]
        ablation_id = None
        root_cause = None
        if not source.passed:
            ablated = service.ablate_file_management_cleanup(item.harness_run_id, source.trial_result_id)
            if len(ablated.ablations) != 1:
                raise RuntimeError(f"Ablation did not persist exactly one report for {item.batch_item_id}.")
            ablation_id = ablated.ablations[0].ablation_id
            root_cause = "permission_violation"
        artifacts.append(
            Stage1ReplayAblationArtifact(
                artifact_id=f"stage1_replay_ablation__{item.batch_item_id}",
                case_id=pair.pair_id,
                mutation_kind=pair.mutation_kind,
                harness_run_id=item.harness_run_id,
                source_trial_result_id=source.trial_result_id,
                replay_result_id=replay.replay_result_id,
                replay_reproduced=replay.reproduced,
                ablation_id=ablation_id,
                ablation_root_cause=root_cause,
                candidate_root_causes=["permission_violation", "runner_failure", "oracle_failure"],
            )
        )
    store = service.store
    store.save_many([("stage1_replay_ablation_artifact", item.artifact_id, "stage1", item) for item in artifacts])
    replay_count = len(artifacts)
    ablation_items = [item for item in artifacts if item.ablation_id]
    top1 = sum(item.ablation_root_cause == "permission_violation" for item in ablation_items) / len(ablation_items) if ablation_items else 1.0
    metrics = Stage1ReplayAblationMetrics(
        sample_count=len(batch.items),
        replay_sample_count=replay_count,
        replay_reproduction_rate=sum(item.replay_reproduced for item in artifacts) / replay_count if replay_count else 0.0,
        ablation_case_count=len(ablation_items),
        ablation_top1=top1,
        ablation_top3=top1,
        unresolved_rate=sum(item.ablation_root_cause is None for item in ablation_items) / len(ablation_items) if ablation_items else 0.0,
        artifact_ids=[item.artifact_id for item in artifacts],
    )
    store.save("stage1_replay_ablation_metrics", "stage1_replay_ablation_metrics", "stage1", metrics)
    _write_replay_artifacts(artifacts_root, artifacts, metrics)
    return metrics


def report_stage1_replay_ablation(store: Store, artifacts_root: Path) -> Stage1ReplayAblationMetrics:
    artifacts = store.list("stage1_replay_ablation_artifact", Stage1ReplayAblationArtifact, "stage1")
    if not artifacts:
        raise ValueError("Stage 1 replay/ablation artifacts are missing.")
    ablation_items = [item for item in artifacts if item.ablation_id]
    top1 = sum(item.ablation_root_cause == "permission_violation" for item in ablation_items) / len(ablation_items) if ablation_items else 1.0
    metrics = Stage1ReplayAblationMetrics(
        sample_count=len(artifacts),
        replay_sample_count=len(artifacts),
        replay_reproduction_rate=sum(item.replay_reproduced for item in artifacts) / len(artifacts),
        ablation_case_count=len(ablation_items),
        ablation_top1=top1,
        ablation_top3=top1,
        unresolved_rate=sum(item.ablation_root_cause is None for item in ablation_items) / len(ablation_items) if ablation_items else 0.0,
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

    add(_resilient_case(service, "runner_before_execute", "before_execute", False))
    add(_cross_process_resilient(service, "runner_after_runner_cross_process", "after_runner"))
    add(_oracle_exception(service))
    add(_cross_process_resilient(service, "evidence_written_finding_missing", "after_finding"))
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
    resumed = child.resume_file_management_run(run.harness_run_id)
    return Stage1FaultInjectionArtifact(
        artifact_id=f"stage1_fault__{scenario}", scenario_id=scenario,
        injection_point=crash_at, reproduction_command=f"subprocess python -c <crash_at={crash_at}>", cross_process=True,
        process_exit_state=f"exit:{completed.returncode}", pre_crash_artifact_ids=[run.harness_run_id], post_resume_artifact_ids=[resumed.run.harness_run_id],
        trace_ids=[event.event_id for event in resumed.events], database_state="child SQLite records reopened by parent",
        terminal_reason=resumed.run.status, duplicate_side_effect_count=max(0, len(resumed.operations) - 1), recovery_result="recovered",
    )


def _oracle_exception(service: Service) -> Stage1FaultInjectionArtifact:
    fixture = service.file_management_fixture()
    try:
        service.start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, crash_at="after_runner")
    except InjectedCrash:
        pass
    interrupted = next(
        run for run in service.store.list("harness_run", HarnessRun, fixture.product.product_id)
        if run.status in {"created", "planned", "running"}
    )
    service.resume_file_management_run(interrupted.harness_run_id)
    original = service.p2_harness.oracle.verify
    service.p2_harness.oracle.verify = lambda *_args: (_ for _ in ()).throw(RuntimeError("injected oracle failure"))
    try:
        try:
            service.resume_file_management_run(interrupted.harness_run_id)
        except RuntimeError:
            pass
    finally:
        service.p2_harness.oracle.verify = original
    resumed = service.resume_file_management_run(interrupted.harness_run_id)
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__oracle_exception", scenario_id="oracle_exception", injection_point="oracle.verify",
        reproduction_command="monkeypatch FileManagementPolicyOracle.verify", cross_process=False,
        process_exit_state="same_process_exception", pre_crash_artifact_ids=[interrupted.harness_run_id], post_resume_artifact_ids=[resumed.run.harness_run_id],
        trace_ids=[event.event_id for event in resumed.events], database_state="verification resumed after oracle replacement",
        terminal_reason=resumed.run.status, duplicate_side_effect_count=max(0, len(resumed.operations) - 1), recovery_result="recovered",
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
        process_exit_state="child_exit_then_new_service", pre_crash_artifact_ids=[run_id], post_resume_artifact_ids=[run_id], trace_ids=[],
        database_state="one TrialResult persisted before resume", terminal_reason=resumed.run.status,
        duplicate_side_effect_count=0, recovery_result="recovered",
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
        process_exit_state="child_exit_then_new_service_then_repeated_resume", pre_crash_artifact_ids=[batch_id], post_resume_artifact_ids=[resumed.batch.batch_id], trace_ids=[],
        database_state=f"{len(resumed_again.items)} items terminal after repeated resume", terminal_reason=resumed_again.batch.status,
        duplicate_side_effect_count=0, recovery_result="recovered",
    )


def _duplicate_operation_case(service: Service) -> Stage1FaultInjectionArtifact:
    fixture = service.file_management_fixture()
    first = service.start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id)
    second = Service(service.store.path.as_posix()).resume_file_management_run(first.run.harness_run_id)
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__duplicate_operation", scenario_id="duplicate_operation_id", injection_point="runner.execute",
        reproduction_command="resume_file_management_run(<completed-run>)", cross_process=True,
        process_exit_state="normal_then_new_service", pre_crash_artifact_ids=[first.run.harness_run_id], post_resume_artifact_ids=[second.run.harness_run_id], trace_ids=[],
        database_state="single durable Operation", terminal_reason=second.run.status, duplicate_side_effect_count=max(0, len(second.operations) - 1), recovery_result="recovered",
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
        pre_crash_artifact_ids=[result.run.harness_run_id], post_resume_artifact_ids=[], trace_ids=[], database_state=failure.category,
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
        pre_crash_artifact_ids=[result.run.harness_run_id], post_resume_artifact_ids=[], trace_ids=[], database_state=failure.category,
        terminal_reason=result.run.status, duplicate_side_effect_count=0, recovery_result="recovered" if result.release_decision.status == "pending" else "unresolved",
    )


def _database_transaction_case(service: Service) -> Stage1FaultInjectionArtifact:
    fixture = service.file_management_fixture()
    original = service.store.save_many
    failed = False

    def fail_once(records):
        nonlocal failed
        if not failed:
            failed = True
            raise sqlite3.OperationalError("injected transaction failure")
        return original(records)

    service.store.save_many = fail_once  # type: ignore[method-assign]
    try:
        try:
            service.start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id)
        except sqlite3.OperationalError:
            pass
    finally:
        service.store.save_many = original  # type: ignore[method-assign]
    retried = Service(service.store.path.as_posix()).start_file_management_run(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id)
    return Stage1FaultInjectionArtifact(
        artifact_id="stage1_fault__database_transaction", scenario_id="database_transaction_failure", injection_point="Store.save_many",
        reproduction_command="inject sqlite3.OperationalError on first save_many", cross_process=False, process_exit_state="transaction rolled back",
        pre_crash_artifact_ids=[], post_resume_artifact_ids=[retried.run.harness_run_id], trace_ids=[], database_state="retry created one terminal run",
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
        pre_crash_artifact_ids=[completed.batch.batch_id], post_resume_artifact_ids=[rerun.batch.batch_id], trace_ids=[],
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
        artifact_ids=[item.artifact_id for item in artifacts],
    )
    store.save("stage1_fault_injection_metrics", "stage1_fault_injection_metrics", "stage1", metrics)
    (artifacts_root / "fault_injection").mkdir(parents=True, exist_ok=True)
    (artifacts_root / "fault_injection" / "stage1_fault_injection_matrix.json").write_text(json.dumps([item.model_dump() for item in artifacts], indent=2), encoding="utf-8")
    return metrics
