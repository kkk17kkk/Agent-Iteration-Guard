from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .domain import (
    ChangeSet,
    ComponentSnapshot,
    Evidence,
    EvalCase,
    EvalPlan,
    ExecutionResult,
    Finding,
    Handoff,
    HarnessRun,
    ReleaseDecision,
    RunEvent,
    VerificationResult,
    WorkItem,
)
from .oracle import PathPolicyOracle
from .routing import build_eval_plan, build_work_items
from .runner import FakeFileRunner


class CoordinationState(TypedDict):
    run: HarnessRun
    handoffs: list[Handoff]


class HarnessCoordinator:
    """Coordinates typed role handoffs without owning storage or agent execution."""

    def __init__(self) -> None:
        graph = StateGraph(CoordinationState)
        graph.add_node("intake", self._intake)
        graph.add_node("plan", self._plan)
        graph.add_node("await_evidence", self._await_evidence)
        graph.add_node("block", self._block)
        graph.add_node("hold_gate", self._hold_gate)
        graph.add_edge(START, "intake")
        graph.add_edge("intake", "plan")
        graph.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {"await_evidence": "await_evidence", "block": "block"},
        )
        graph.add_edge("await_evidence", "hold_gate")
        graph.add_edge("block", END)
        graph.add_edge("hold_gate", END)
        self.graph = graph.compile()

    def prepare(self, run: HarnessRun) -> tuple[HarnessRun, list[Handoff]]:
        result = self.graph.invoke({"run": run, "handoffs": []})
        return result["run"], result["handoffs"]

    @staticmethod
    def _intake(state: CoordinationState) -> dict[str, list[Handoff]]:
        run = state["run"]
        return {
            "handoffs": [
                Handoff(
                    harness_run_id=run.harness_run_id,
                    from_role="intake",
                    to_role="planner",
                    kind="evaluation_scope",
                    summary="Validated the product version and loaded the registered evaluation scope.",
                    eval_case_ids=run.eval_case_ids,
                )
            ]
        }

    @staticmethod
    def _plan(state: CoordinationState) -> dict[str, object]:
        run = state["run"].model_copy(update={"status": "planned"})
        handoffs = [
            *state["handoffs"],
            Handoff(
                harness_run_id=run.harness_run_id,
                from_role="planner",
                to_role="executor",
                kind="evaluation_plan",
                summary="Phase 1 selects every registered case because ChangeSet routing is not available yet.",
                eval_case_ids=run.eval_case_ids,
            ),
        ]
        return {"run": run, "handoffs": handoffs}

    @staticmethod
    def _route_after_plan(state: CoordinationState) -> str:
        return "await_evidence" if state["run"].eval_case_ids else "block"

    @staticmethod
    def _await_evidence(state: CoordinationState) -> dict[str, object]:
        run = state["run"].model_copy(update={"status": "awaiting_evidence"})
        handoff = Handoff(
            harness_run_id=run.harness_run_id,
            from_role="executor",
            to_role="verifier",
            kind="evidence_request",
            summary="Execution evidence is required before verification or release assessment.",
            eval_case_ids=run.eval_case_ids,
        )
        return {"run": run, "handoffs": [*state["handoffs"], handoff]}

    @staticmethod
    def _block(state: CoordinationState) -> dict[str, object]:
        reason = "No evaluation cases are registered for this product version."
        run = state["run"].model_copy(update={"status": "blocked", "blocked_reason": reason})
        handoff = Handoff(
            harness_run_id=run.harness_run_id,
            from_role="gatekeeper",
            to_role="planner",
            kind="gate_block",
            summary=reason,
        )
        return {"run": run, "handoffs": [*state["handoffs"], handoff]}

    @staticmethod
    def _hold_gate(state: CoordinationState) -> dict[str, list[Handoff]]:
        run = state["run"]
        handoff = Handoff(
            harness_run_id=run.harness_run_id,
            from_role="verifier",
            to_role="gatekeeper",
            kind="release_hold",
            summary="Release assessment is pending verified execution evidence.",
            eval_case_ids=run.eval_case_ids,
        )
        return {"handoffs": [*state["handoffs"], handoff]}


class P0State(TypedDict):
    run: HarnessRun
    changeset: ChangeSet
    eval_cases: list[EvalCase]
    candidate_snapshot: ComponentSnapshot
    eval_plan: EvalPlan | None
    work_items: list[WorkItem]
    executions: list[ExecutionResult]
    verifications: list[VerificationResult]
    evidence: list[Evidence]
    findings: list[Finding]
    decision: ReleaseDecision | None
    events: list[RunEvent]
    requested_eval_case_ids: list[str] | None


class P0HarnessCoordinator:
    """Runs the deterministic P0 File Agent lifecycle over typed artifacts."""

    def __init__(self) -> None:
        self.runner = FakeFileRunner()
        self.oracle = PathPolicyOracle()
        graph = StateGraph(P0State)
        graph.add_node("plan", self._plan)
        graph.add_node("execute", self._execute)
        graph.add_node("verify", self._verify)
        graph.add_node("gate", self._gate)
        graph.add_node("record_blocked", self._record_blocked)
        graph.add_node("record_ready", self._record_ready)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", "verify")
        graph.add_edge("verify", "gate")
        graph.add_conditional_edges(
            "gate",
            self._route_after_gate,
            {"record_blocked": "record_blocked", "record_ready": "record_ready"},
        )
        graph.add_edge("record_blocked", END)
        graph.add_edge("record_ready", END)
        self.graph = graph.compile()

    def run(
        self,
        run: HarnessRun,
        changeset: ChangeSet,
        eval_cases: list[EvalCase],
        candidate_snapshot: ComponentSnapshot,
        requested_eval_case_ids: list[str] | None = None,
    ) -> P0State:
        created = RunEvent(
            harness_run_id=run.harness_run_id,
            sequence=1,
            event_type="RUN_CREATED",
            artifact_ids=[changeset.changeset_id],
        )
        return self.graph.invoke(
            {
                "run": run,
                "changeset": changeset,
                "eval_cases": eval_cases,
                "candidate_snapshot": candidate_snapshot,
                "eval_plan": None,
                "work_items": [],
                "executions": [],
                "verifications": [],
                "evidence": [],
                "findings": [],
                "decision": None,
                "events": [created],
                "requested_eval_case_ids": requested_eval_case_ids,
            }
        )

    @staticmethod
    def _event(state: P0State, event_type: str, artifact_ids: list[str]) -> RunEvent:
        return RunEvent(
            harness_run_id=state["run"].harness_run_id,
            sequence=len(state["events"]) + 1,
            event_type=event_type,  # type: ignore[arg-type]
            artifact_ids=artifact_ids,
        )

    def _plan(self, state: P0State) -> dict[str, object]:
        run = state["run"].model_copy(update={"status": "planning"})
        plan = build_eval_plan(state["changeset"], state["eval_cases"])
        requested = state["requested_eval_case_ids"]
        if requested is not None:
            requested_set = set(requested)
            plan = plan.model_copy(
                update={
                    "items": [
                        item.model_copy(
                            update={
                                "selected": item.eval_case_id in requested_set,
                                "reason": "Full regression control requested by Stage 1." if item.eval_case_id in requested_set else "Excluded from full regression control override.",
                            }
                        )
                        for item in plan.items
                    ]
                }
            )
        work_items = build_work_items(run.harness_run_id, plan)
        run = run.model_copy(update={"status": "planned", "eval_case_ids": plan.selected_case_ids})
        event = self._event(state, "PLAN_CREATED", [plan.eval_plan_id, *[item.work_item_id for item in work_items]])
        return {"run": run, "eval_plan": plan, "work_items": work_items, "events": [*state["events"], event]}

    def _execute(self, state: P0State) -> dict[str, object]:
        run = state["run"].model_copy(update={"status": "running"})
        work_items = [item.model_copy(update={"status": "completed"}) for item in state["work_items"]]
        executions = [self.runner.execute(item, state["candidate_snapshot"]) for item in work_items]
        event = self._event(state, "TRIALS_COMPLETED", [execution.execution_id for execution in executions])
        return {"run": run, "work_items": work_items, "executions": executions, "events": [*state["events"], event]}

    def _verify(self, state: P0State) -> dict[str, object]:
        run = state["run"].model_copy(update={"status": "verifying"})
        work_by_id = {item.work_item_id: item for item in state["work_items"]}
        verifications = [self.oracle.verify(run.harness_run_id, execution) for execution in state["executions"]]
        evidence = [
            Evidence(
                harness_run_id=run.harness_run_id,
                eval_case_id=work_by_id[execution.work_item_id].eval_case_id,
                source="oracle",
                level="verified",
                summary=verification.observed,
                execution_id=execution.execution_id,
                verification_id=verification.verification_id,
            )
            for execution, verification in zip(state["executions"], verifications, strict=True)
        ]
        event = self._event(state, "VERIFICATION_COMPLETED", [verification.verification_id for verification in verifications])
        return {"run": run, "verifications": verifications, "evidence": evidence, "events": [*state["events"], event]}

    def _gate(self, state: P0State) -> dict[str, object]:
        run = state["run"].model_copy(update={"status": "deciding"})
        evidence_by_verification = {item.verification_id: item for item in state["evidence"]}
        findings = [
            Finding(
                product_id=run.product_id,
                harness_run_id=run.harness_run_id,
                title=verification.observed,
                evidence_level="verified",
                evidence_ids=[evidence_by_verification[verification.verification_id].evidence_id],
                severity=verification.severity,
            )
            for verification in state["verifications"]
            if not verification.passed
        ]
        decision = ReleaseDecision(
            product_id=run.product_id,
            version_id=run.candidate_version_id or run.version_id,
            harness_run_id=run.harness_run_id,
            status="blocked" if findings else "ready",
            rationale="Critical deterministic path-policy regression detected." if findings else "All selected deterministic oracles passed.",
            finding_ids=[finding.finding_id for finding in findings],
        )
        events = [*state["events"]]
        if findings:
            events.append(self._event(state, "FINDING_CREATED", [finding.finding_id for finding in findings]))
        events.append(self._event({**state, "events": events}, "RELEASE_DECIDED", [decision.decision_id]))
        return {"run": run, "findings": findings, "decision": decision, "events": events}

    @staticmethod
    def _route_after_gate(state: P0State) -> str:
        return "record_blocked" if state["decision"].status == "blocked" else "record_ready"

    def _record_blocked(self, state: P0State) -> dict[str, object]:
        reason = "critical_regression"
        run = state["run"].model_copy(update={"status": "blocked", "blocked_reason": reason})
        event = self._event(state, "RUN_RECORDED", [state["decision"].decision_id])
        return {"run": run, "events": [*state["events"], event]}

    def _record_ready(self, state: P0State) -> dict[str, object]:
        run = state["run"].model_copy(update={"status": "recorded"})
        event = self._event(state, "RUN_RECORDED", [state["decision"].decision_id])
        return {"run": run, "events": [*state["events"], event]}
