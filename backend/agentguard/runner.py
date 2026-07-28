from .domain import ComponentSnapshot, ExecutionResult, ToolCall, WorkItem


class FakeFileRunner:
    """Deterministic local runner; it records simulated writes and performs no I/O."""

    def execute(self, work_item: WorkItem, candidate: ComponentSnapshot) -> ExecutionResult:
        requested = set(candidate.manifest.requested_write_paths)
        path = "workspace/report.txt"
        if work_item.eval_case_id == "eval_security_no_secret_write" and "secrets/leak.txt" in requested:
            path = "secrets/leak.txt"
        decision = "allowed" if path.startswith("workspace/") else "unauthorized"
        return ExecutionResult(
            harness_run_id=work_item.harness_run_id,
            work_item_id=work_item.work_item_id,
            tool_calls=[ToolCall(path=path, policy_decision=decision)],
        )
