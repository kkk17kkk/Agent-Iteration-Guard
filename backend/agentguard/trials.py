import hashlib
import json
from time import perf_counter

from .domain import (
    ComponentSnapshot,
    Evidence,
    ExecutionResult,
    HarnessRun,
    RunEvent,
    ToolPolicy,
    TrialMetrics,
    TrialResult,
    TrialSpec,
    VerificationResult,
    WorkItem,
)
from .oracle import FileManagementPolicyOracle
from .runner import LocalFileRunner
from .store import Store


ENVIRONMENT_FINGERPRINT = hashlib.sha256(
    b"README.md:# Original\nManaged by the fixture.\ntemporary.txt:temporary\n"
).hexdigest()


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class FileTrialEvaluator:
    """Runs saved TrialSpec inputs through the real local runner and aggregates facts."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.runner = LocalFileRunner(store)
        self.oracle = FileManagementPolicyOracle()

    def execute(
        self,
        run: HarnessRun,
        spec: TrialSpec,
        work_item: WorkItem,
        candidate: ComponentSnapshot,
        policy: ToolPolicy,
        runner: object | None = None,
    ) -> TrialResult:
        self._event(run, "TRIAL_STARTED", [spec.trial_id])
        started = perf_counter()
        active_runner = runner or self.runner
        execution = active_runner.execute(run, work_item, candidate, policy, spec.cleanup_attempt)
        latency_ms = (perf_counter() - started) * 1000
        verification = self.oracle.verify(run.harness_run_id, execution)
        evidence = Evidence(
            harness_run_id=run.harness_run_id,
            eval_case_id=work_item.eval_case_id,
            source="oracle",
            level="verified",
            summary=verification.observed,
            execution_id=execution.execution_id,
            verification_id=verification.verification_id,
        )
        result = TrialResult(
            harness_run_id=run.harness_run_id,
            trial_id=spec.trial_id,
            kind=spec.kind,
            execution_id=execution.execution_id,
            verification_id=verification.verification_id,
            evidence_id=evidence.evidence_id,
            passed=verification.passed,
            latency_ms=latency_ms,
            cost_usd=execution.external_cost_usd,
            trace_fingerprint=fingerprint([call.model_dump() for call in execution.tool_calls]),
        )
        self.store.save_many([
            ("verification", verification.verification_id, run.product_id, verification),
            ("evidence", evidence.evidence_id, run.product_id, evidence),
            ("trial_result", result.trial_result_id, run.product_id, result),
        ])
        self._event(run, "TRIAL_COMPLETED", [spec.trial_id, result.trial_result_id])
        return result

    def aggregate(self, run: HarnessRun, results: list[TrialResult]) -> TrialMetrics:
        outcomes = [1.0 if result.passed else 0.0 for result in results]
        success_rate = sum(outcomes) / len(outcomes)
        variance = sum((outcome - success_rate) ** 2 for outcome in outcomes) / len(outcomes)
        metrics = TrialMetrics(
            harness_run_id=run.harness_run_id,
            trial_result_ids=[result.trial_result_id for result in results],
            trial_count=len(results),
            success_rate=success_rate,
            variance=variance,
            mean_latency_ms=sum(result.latency_ms for result in results) / len(results),
            total_cost_usd=sum(result.cost_usd for result in results),
        )
        self.store.save("trial_metrics", metrics.metrics_id, run.product_id, metrics)
        self._event(run, "METRICS_RECORDED", [metrics.metrics_id])
        return metrics

    def _event(self, run: HarnessRun, event_type: str, artifact_ids: list[str]) -> None:
        events = [
            event
            for event in self.store.list("run_event", RunEvent, run.product_id)
            if event.harness_run_id == run.harness_run_id
        ]
        event = RunEvent(
            harness_run_id=run.harness_run_id,
            sequence=max((item.sequence for item in events), default=0) + 1,
            event_type=event_type,  # type: ignore[arg-type]
            artifact_ids=artifact_ids,
        )
        self.store.save("run_event", event.event_id, run.product_id, event)


def policy_fingerprint(policy: ToolPolicy) -> str:
    return fingerprint({
        "allowed_read_paths": policy.allowed_read_paths,
        "allowed_write_paths": policy.allowed_write_paths,
        "allow_delete": policy.allow_delete,
        "sandbox_kind": policy.sandbox_kind,
    })
