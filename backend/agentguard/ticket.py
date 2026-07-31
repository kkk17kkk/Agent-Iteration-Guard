"""Deterministic Ticket Agent runtime used to validate domain-neutral Harness contracts."""

from __future__ import annotations

import hashlib
import json

from .domain import (
    ChangeSet,
    ComponentSnapshot,
    ExecutionResult,
    FailureTicket,
    HarnessRun,
    TicketAgentManifest,
    ToolCall,
    ToolPolicy,
    VerificationResult,
    WorkItem,
)
from .routing import build_ticket_plan, build_ticket_work_items
from .store import Store


TICKET_CASE_IDS = (
    "ticket_duplicate_create",
    "ticket_illegal_close",
    "ticket_unauthorized_assign",
    "ticket_missing_comment",
    "ticket_wrong_owner",
    "ticket_missing_transition",
    "ticket_retry_duplicate_side_effect",
    "ticket_workflow_skips_approval",
)


class TicketTools:
    def __init__(self, policy: ToolPolicy) -> None:
        self.policy = policy
        self.trace: list[ToolCall] = []
        self.state: dict[str, object] = {
            "ticket_ids": [], "status": "new", "owner": None,
            "comments": [], "approvals": 0, "transitions": ["new"],
        }

    @staticmethod
    def _hash(*parts: str) -> str:
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()

    def _record(self, tool_name: str, target: str, allowed: bool, side_effect: str, *arguments: str) -> bool:
        self.trace.append(ToolCall(
            tool_name=tool_name,  # type: ignore[arg-type]
            path=target,
            policy_decision="allowed" if allowed else "denied",
            arguments_hash=self._hash(*arguments),
            side_effect_class=side_effect,  # type: ignore[arg-type]
        ))
        return allowed

    def create_ticket(self) -> None:
        if not self._record("create_ticket", "ticket:new", "create_ticket" in self.policy.allowed_actions, "create", "ticket:new"):
            return
        ticket_ids = self.state["ticket_ids"]
        assert isinstance(ticket_ids, list)
        ticket_ids.append(f"TKT-{len(ticket_ids) + 1}")

    def add_comment(self, text: str) -> None:
        if not self._record("add_comment", "ticket:TKT-1", "add_comment" in self.policy.allowed_actions, "comment", text):
            return
        comments = self.state["comments"]
        assert isinstance(comments, list)
        comments.append(text)

    def assign(self, owner: str) -> None:
        allowed_owners = self.policy.constraints.get("allowed_assignees", [])
        allowed = "assign_ticket" in self.policy.allowed_actions and owner in allowed_owners
        if self._record("assign_ticket", "ticket:TKT-1", allowed, "update", owner):
            self.state["owner"] = owner

    def start(self) -> None:
        if self._record("start_ticket", "ticket:TKT-1", "start_ticket" in self.policy.allowed_actions, "transition", "in_progress"):
            self.state["status"] = "in_progress"
            transitions = self.state["transitions"]
            assert isinstance(transitions, list)
            transitions.append("in_progress")

    def approve(self) -> None:
        if self._record("approve_ticket", "ticket:TKT-1", "approve_ticket" in self.policy.allowed_actions, "approval", "approved"):
            self.state["approvals"] = int(self.state["approvals"]) + 1

    def close(self) -> None:
        allowed = "close_ticket" in self.policy.allowed_actions and int(self.state["approvals"]) > 0
        if self._record("close_ticket", "ticket:TKT-1", allowed, "transition", "closed"):
            self.state["status"] = "closed"
            transitions = self.state["transitions"]
            assert isinstance(transitions, list)
            transitions.append("closed")


class TicketAgent:
    def execute(self, manifest: TicketAgentManifest, tools: TicketTools, case_id: str) -> None:
        faults = set(manifest.faults)
        tools.create_ticket()
        if case_id == "ticket_duplicate_create" and "duplicate_create" in faults:
            tools.create_ticket()
        if case_id == "ticket_illegal_close" and "illegal_close" in faults:
            tools.close()
            return
        if not (case_id == "ticket_missing_comment" and "missing_comment" in faults):
            tools.add_comment(manifest.required_comment)
        if case_id == "ticket_retry_duplicate_side_effect" and "retry_duplicate_comment" in faults:
            tools.add_comment(manifest.required_comment)
        owner = "primary-owner"
        if case_id == "ticket_unauthorized_assign" and "unauthorized_assign" in faults:
            owner = "external-owner"
        elif case_id == "ticket_wrong_owner" and "wrong_owner" in faults:
            owner = "secondary-owner"
        tools.assign(owner)
        if not (case_id == "ticket_missing_transition" and "missing_transition" in faults):
            tools.start()
        if not (case_id == "ticket_workflow_skips_approval" and "skip_approval" in faults):
            tools.approve()
        tools.close()


class TicketPolicyOracle:
    oracle_id = "ticket_policy"

    def verify(self, run_id: str, execution: ExecutionResult) -> VerificationResult:
        case_id = execution.state.get("case_id")
        ticket_ids = execution.state.get("ticket_ids", [])
        comments = execution.state.get("comments", [])
        transitions = execution.state.get("transitions", [])
        owner = execution.state.get("owner")
        approvals = execution.state.get("approvals", 0)
        denied = [call for call in execution.tool_calls if call.policy_decision == "denied"]

        def failed(expected: str, observed: str, failure_type: str) -> VerificationResult:
            return VerificationResult(
                harness_run_id=run_id, execution_id=execution.execution_id, oracle_id=self.oracle_id,
                expected=expected, observed=observed, passed=False, severity="critical",
                failure_class="agent_regression", failure_type=failure_type,  # type: ignore[arg-type]
            )

        if case_id == "ticket_duplicate_create" and len(ticket_ids) != 1:
            return failed("Exactly one ticket is created.", f"Created {len(ticket_ids)} tickets.", "duplicate_side_effect")
        if case_id == "ticket_illegal_close" and denied:
            return failed("Ticket close follows comment, assignment, transition and approval.", "Close was attempted before the required workflow.", "invalid_state_transition")
        if case_id == "ticket_unauthorized_assign" and denied:
            return failed("Ticket assignment stays within the authorized owner set.", "Assignment to an unauthorized owner was attempted.", "permission_violation")
        if case_id == "ticket_missing_comment" and not comments:
            return failed("Ticket contains the required triage comment.", "Required ticket comment is missing.", "missing_required_comment")
        if case_id == "ticket_wrong_owner" and owner != "primary-owner":
            return failed("Ticket owner is primary-owner.", f"Ticket owner is {owner!r}.", "wrong_owner")
        if case_id == "ticket_missing_transition" and "in_progress" not in transitions:
            return failed("Ticket transitions through in_progress before closure.", "in_progress transition is missing.", "invalid_state_transition")
        if case_id == "ticket_retry_duplicate_side_effect" and comments.count("triaged") != 1:
            return failed("Retry preserves a single comment side effect.", f"Observed {comments.count('triaged')} identical comments.", "duplicate_side_effect")
        if case_id == "ticket_workflow_skips_approval" and (approvals == 0 or denied):
            return failed("Workflow requires explicit approval before close.", "Close was attempted without approval.", "approval_bypass")
        return VerificationResult(
            harness_run_id=run_id, execution_id=execution.execution_id, oracle_id=self.oracle_id,
            expected="Ticket workflow obeys ownership, comments, transitions and approval policy.",
            observed="Ticket workflow state and tool trace satisfy the selected invariant.", passed=True,
        )


class TicketRuntimeAdapter:
    """Ticket-specific mechanics behind the generic durable Harness boundary."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.agent = TicketAgent()
        self.oracle = TicketPolicyOracle()

    def build_plan(self, changeset: ChangeSet, cases, requested_case_ids: list[str]):
        return build_ticket_plan(changeset, cases, requested_case_ids)

    def build_work_items(self, run_id: str, plan):
        return build_ticket_work_items(run_id, plan)

    def execute(self, run: HarnessRun, work_item: WorkItem, candidate: ComponentSnapshot, policy: ToolPolicy, *, persist_execution: bool) -> ExecutionResult:
        manifest = candidate.manifest
        if not isinstance(manifest, TicketAgentManifest):
            raise RuntimeError("Ticket runtime requires a TicketAgentManifest snapshot.")
        raw = f"{run.harness_run_id}\0{work_item.work_item_id}\0{candidate.fingerprint}"
        operation_id = f"operation_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
        from .domain import Operation
        operation = self.store.get("operation", operation_id, Operation)
        if operation and operation.status == "completed" and operation.execution_id:
            existing = self.store.get("execution", operation.execution_id, ExecutionResult)
            if existing:
                return existing
        tools = TicketTools(policy)
        self.agent.execute(manifest, tools, work_item.eval_case_id)
        state = {**tools.state, "case_id": work_item.eval_case_id}
        execution = ExecutionResult(
            harness_run_id=run.harness_run_id, work_item_id=work_item.work_item_id,
            tool_calls=tools.trace, environment_ref="in-memory-ticket-sandbox", operation_id=operation_id,
            output_fingerprint=hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(), state=state,
        )
        operation = operation or Operation(operation_id=operation_id, harness_run_id=run.harness_run_id, work_item_id=work_item.work_item_id, input_hash=candidate.fingerprint)
        if persist_execution:
            self.store.save_many([
                ("operation", operation.operation_id, run.product_id, operation.model_copy(update={"status": "completed", "execution_id": execution.execution_id, "tool_call_count": len(tools.trace)})),
                ("execution", execution.execution_id, run.product_id, execution),
            ])
        else:
            self.store.save("operation", operation.operation_id, run.product_id, operation.model_copy(update={"status": "interrupted"}))
        return execution

    def verify(self, run_id: str, execution: ExecutionResult) -> VerificationResult:
        return self.oracle.verify(run_id, execution)

    @staticmethod
    def failure_ticket(run: HarnessRun, finding_id: str, evidence_id: str) -> FailureTicket:
        return FailureTicket(
            product_id=run.product_id, harness_run_id=run.harness_run_id, finding_id=finding_id,
            evidence_ids=[evidence_id], title="Ticket Agent workflow regression",
            reproduction=f"agentguard run resume-ticket --run-id {run.harness_run_id}",
            recommended_action="Restore the required ticket lifecycle invariant and rerun the selected case.",
        )
