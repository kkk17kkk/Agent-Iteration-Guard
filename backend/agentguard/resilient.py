from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .domain import (
    ChangeSet,
    ComponentSnapshot,
    EvalCase,
    Evidence,
    ExecutionResult,
    FailureTicket,
    Finding,
    HarnessRun,
    ReleaseDecision,
    RunCheckpoint,
    RunEvent,
    ToolPolicy,
    VerificationResult,
    WorkItem,
)
from .oracle import FileManagementPolicyOracle
from .routing import build_file_management_plan, build_file_management_work_items
from .runner import LocalFileRunner, RunnerInterrupted
from .store import Store


Step = Literal["plan", "execute", "verify", "gate", "record", "completed"]
CrashPoint = Literal["before_execute", "after_runner", "after_finding"]


class InjectedCrash(RuntimeError):
    pass


class DurableState(TypedDict):
    harness_run_id: str
    next_step: Step
    crash_at: CrashPoint | None


class ResilientFileHarness:
    """Runs one durable LangGraph node at a time, keyed by an SQLite checkpoint."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.runner = LocalFileRunner(store)
        self.oracle = FileManagementPolicyOracle()
        graph = StateGraph(DurableState)
        graph.add_node("plan", self._plan)
        graph.add_node("execute", self._execute)
        graph.add_node("verify", self._verify)
        graph.add_node("gate", self._gate)
        graph.add_node("record", self._record)
        graph.add_conditional_edges(START, self._route, {
            "plan": "plan", "execute": "execute", "verify": "verify", "gate": "gate", "record": "record",
        })
        for step in ("plan", "execute", "verify", "gate", "record"):
            graph.add_edge(step, END)
        self.graph = graph.compile()

    def advance(self, run: HarnessRun, crash_at: CrashPoint | None = None) -> HarnessRun:
        checkpoint = self.checkpoint(run)
        if checkpoint.next_step == "completed":
            return run
        self.graph.invoke({
            "harness_run_id": run.harness_run_id,
            "next_step": checkpoint.next_step,
            "crash_at": crash_at,
        })
        updated = self.store.get("harness_run", run.harness_run_id, HarnessRun)
        if not updated:
            raise RuntimeError(f"Harness run disappeared: {run.harness_run_id}")
        return updated

    def checkpoint(self, run: HarnessRun) -> RunCheckpoint:
        checkpoints = [
            item
            for item in self.store.list("checkpoint", RunCheckpoint, run.product_id)
            if item.harness_run_id == run.harness_run_id
        ]
        if not checkpoints:
            raise RuntimeError(f"No durable checkpoint for run: {run.harness_run_id}")
        return max(checkpoints, key=lambda item: item.event_sequence)

    @staticmethod
    def _route(state: DurableState) -> Step:
        return state["next_step"]

    def _events(self, run: HarnessRun) -> list[RunEvent]:
        return [
            event
            for event in self.store.list("run_event", RunEvent, run.product_id)
            if event.harness_run_id == run.harness_run_id
        ]

    def _commit(
        self,
        run: HarnessRun,
        next_step: Step,
        event_type: str,
        artifact_ids: list[str],
        records: list[tuple[str, str, str, object]],
    ) -> None:
        sequence = max((event.sequence for event in self._events(run)), default=0)
        stage_event = RunEvent(
            harness_run_id=run.harness_run_id,
            sequence=sequence + 1,
            event_type=event_type,  # type: ignore[arg-type]
            artifact_ids=artifact_ids,
        )
        checkpoint = RunCheckpoint(
            harness_run_id=run.harness_run_id,
            next_step=next_step,
            event_sequence=sequence + 2,
        )
        checkpoint_event = RunEvent(
            harness_run_id=run.harness_run_id,
            sequence=sequence + 2,
            event_type="CHECKPOINT_COMMITTED",
            artifact_ids=[checkpoint.checkpoint_id],
        )
        typed_records = records + [
            ("run_event", stage_event.event_id, run.product_id, stage_event),
            ("checkpoint", checkpoint.checkpoint_id, run.product_id, checkpoint),
        ]
        self.store.save_many(typed_records)  # type: ignore[arg-type]
        self.store.save("run_event", checkpoint_event.event_id, run.product_id, checkpoint_event)

    def _changeset(self, run: HarnessRun) -> ChangeSet:
        if run.changeset_id:
            changeset = self.store.get("changeset", run.changeset_id, ChangeSet)
            if changeset:
                return changeset
        for changeset in self.store.list("changeset", ChangeSet, run.product_id):
            if (
                changeset.baseline_version_id == run.baseline_version_id
                and changeset.candidate_version_id == run.candidate_version_id
            ):
                return changeset
        raise RuntimeError("No ChangeSet matches the resilient run.")

    def _candidate(self, changeset: ChangeSet) -> ComponentSnapshot:
        return changeset.candidate_snapshot

    def _work_item(self, run: HarnessRun) -> WorkItem:
        items = [
            item
            for item in self.store.list("work_item", WorkItem, run.product_id)
            if item.harness_run_id == run.harness_run_id
        ]
        if len(items) != 1:
            raise RuntimeError("Resilient File Management run requires exactly one work item.")
        return items[0]

    def _execution(self, run: HarnessRun) -> ExecutionResult:
        items = [
            item
            for item in self.store.list("execution", ExecutionResult, run.product_id)
            if item.harness_run_id == run.harness_run_id
        ]
        if len(items) != 1:
            raise RuntimeError("Resilient File Management run requires exactly one execution.")
        return items[0]

    def _policy(self, run: HarnessRun) -> ToolPolicy:
        policies = [
            policy
            for policy in self.store.list("tool_policy", ToolPolicy, run.product_id)
            if policy.harness_run_id == run.harness_run_id
        ]
        if len(policies) != 1:
            raise RuntimeError("Resilient File Management run requires exactly one tool policy.")
        return policies[0]

    def _plan(self, state: DurableState) -> DurableState:
        run = self._load_run(state["harness_run_id"])
        changeset = self._changeset(run)
        cases = self.store.list("eval_case", EvalCase, run.product_id)
        plan = build_file_management_plan(changeset, cases)
        work_items = build_file_management_work_items(run.harness_run_id, plan)
        if len(work_items) != 1:
            raise RuntimeError("File Management plan did not produce exactly one work item.")
        updated = run.model_copy(update={"status": "planned", "eval_case_ids": plan.selected_case_ids})
        self._commit(
            updated,
            "execute",
            "PLAN_CREATED",
            [plan.eval_plan_id, work_items[0].work_item_id],
            [
                ("harness_run", updated.harness_run_id, updated.product_id, updated),
                ("eval_plan", plan.eval_plan_id, updated.product_id, plan),
                ("work_item", work_items[0].work_item_id, updated.product_id, work_items[0]),
            ],
        )
        return state

    def _execute(self, state: DurableState) -> DurableState:
        run = self._load_run(state["harness_run_id"])
        if state["crash_at"] == "before_execute":
            raise InjectedCrash("Injected crash before the runner starts.")
        work_item = self._work_item(run)
        try:
            execution = self.runner.execute(run, work_item, self._candidate(self._changeset(run)), self._policy(run))
        except RunnerInterrupted:
            failed = run.model_copy(update={"status": "failed", "blocked_reason": "runner_interrupted"})
            self._commit(
                failed,
                "completed",
                "RUN_RECORDED",
                [],
                [("harness_run", failed.harness_run_id, failed.product_id, failed)],
            )
            return state
        if state["crash_at"] == "after_runner":
            raise InjectedCrash("Injected crash after durable runner result and before graph commit.")
        completed_item = work_item.model_copy(update={"status": "completed"})
        updated = run.model_copy(update={"status": "running"})
        self._commit(
            updated,
            "verify",
            "TRIALS_COMPLETED",
            [execution.execution_id],
            [
                ("harness_run", updated.harness_run_id, updated.product_id, updated),
                ("work_item", completed_item.work_item_id, updated.product_id, completed_item),
            ],
        )
        return state

    def _verify(self, state: DurableState) -> DurableState:
        run = self._load_run(state["harness_run_id"])
        execution = self._execution(run)
        verification = self.oracle.verify(run.harness_run_id, execution)
        evidence = Evidence(
            harness_run_id=run.harness_run_id,
            eval_case_id=self._work_item(run).eval_case_id,
            source="oracle",
            level="verified",
            summary=verification.observed,
            execution_id=execution.execution_id,
            verification_id=verification.verification_id,
        )
        updated = run.model_copy(update={"status": "verifying"})
        self._commit(
            updated,
            "gate",
            "VERIFICATION_COMPLETED",
            [verification.verification_id, evidence.evidence_id],
            [
                ("harness_run", updated.harness_run_id, updated.product_id, updated),
                ("verification", verification.verification_id, updated.product_id, verification),
                ("evidence", evidence.evidence_id, updated.product_id, evidence),
            ],
        )
        return state

    def _gate(self, state: DurableState) -> DurableState:
        run = self._load_run(state["harness_run_id"])
        verification = next(
            item
            for item in self.store.list("verification", VerificationResult, run.product_id)
            if item.harness_run_id == run.harness_run_id
        )
        evidence = next(
            item
            for item in self.store.list("evidence", Evidence, run.product_id)
            if item.harness_run_id == run.harness_run_id
        )
        findings: list[Finding] = []
        records: list[tuple[str, str, str, object]] = []
        if not verification.passed:
            finding = Finding(
                product_id=run.product_id,
                harness_run_id=run.harness_run_id,
                title=verification.observed,
                evidence_level="verified",
                evidence_ids=[evidence.evidence_id],
                severity=verification.severity,
            )
            ticket = FailureTicket(
                product_id=run.product_id,
                harness_run_id=run.harness_run_id,
                finding_id=finding.finding_id,
                evidence_ids=[evidence.evidence_id],
                title="File Management Agent permission violation",
                reproduction=f"agentguard run resume --run-id {run.harness_run_id}",
                recommended_action="Remove the cleanup instruction or require explicit delete approval.",
            )
            findings.append(finding)
            records.extend([
                ("finding", finding.finding_id, run.product_id, finding),
                ("failure_ticket", ticket.ticket_id, run.product_id, ticket),
            ])
        updated = run.model_copy(update={"status": "deciding"})
        records.insert(0, ("harness_run", updated.harness_run_id, updated.product_id, updated))
        self._commit(updated, "record", "FAILURE_TICKET_CREATED", [item.finding_id for item in findings], records)
        if state["crash_at"] == "after_finding":
            raise InjectedCrash("Injected crash after Failure Ticket and before Release Decision.")
        return state

    def _record(self, state: DurableState) -> DurableState:
        run = self._load_run(state["harness_run_id"])
        findings = [
            item
            for item in self.store.list("finding", Finding, run.product_id)
            if item.harness_run_id == run.harness_run_id
        ]
        decision = ReleaseDecision(
            product_id=run.product_id,
            version_id=run.candidate_version_id or run.version_id,
            harness_run_id=run.harness_run_id,
            status="blocked" if findings else "ready",
            rationale="Deterministic tool policy violation detected." if findings else "All deterministic tool policy checks passed.",
            finding_ids=[finding.finding_id for finding in findings],
        )
        updated = run.model_copy(update={
            "status": "blocked" if findings else "recorded",
            "blocked_reason": "critical_regression" if findings else None,
        })
        self._commit(
            updated,
            "completed",
            "RELEASE_DECIDED",
            [decision.decision_id],
            [
                ("harness_run", updated.harness_run_id, updated.product_id, updated),
                ("release_decision", decision.decision_id, updated.product_id, decision),
            ],
        )
        return state

    def _load_run(self, harness_run_id: str) -> HarnessRun:
        run = self.store.get("harness_run", harness_run_id, HarnessRun)
        if not run:
            raise RuntimeError(f"Harness run not found: {harness_run_id}")
        return run
