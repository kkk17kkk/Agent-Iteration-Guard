"""Deterministic, event-driven evaluation replanning.

This module deliberately has no model or planner dependency.  It translates one
durable harness event into at most one bounded follow-up item and a complete
audit record; execution remains the Service's responsibility.
"""

from dataclasses import dataclass

from .domain import (
    EnvironmentCapture,
    EvalCase,
    EvalPlan,
    EvalPlanItem,
    ExecutionResult,
    ReplanBudget,
    ReplanRecord,
    ReplayResult,
    RunnerFailure,
    TrialMetrics,
    VerificationResult,
    WorkItem,
)


@dataclass(frozen=True)
class ReplanOutcome:
    record: ReplanRecord
    work_items: list[WorkItem]
    eval_cases: list[EvalCase]
    environment_capture: EnvironmentCapture | None = None
    execute_with_local_runner: bool = False


class ControlledReplanEngine:
    """Rule table for the only Stage 5 replanning transitions."""

    def propose(
        self,
        *,
        run_id: str,
        product_id: str,
        plan: EvalPlan,
        budget: ReplanBudget,
        executions: list[ExecutionResult],
        metrics: list[TrialMetrics],
        verifications: list[VerificationResult],
        runner_failures: list[RunnerFailure],
        replays: list[ReplayResult],
        handled_source_ids: set[str],
        allow_runner_switch: bool,
        environment_fingerprint: str,
        policy_fingerprint: str,
    ) -> ReplanOutcome | None:
        """Return the next deterministic adjustment, in safety-first priority order."""
        for replay in replays:
            if not replay.reproduced and replay.replay_result_id not in handled_source_ids:
                return self._replay_unresolved(
                    run_id, product_id, plan, budget, replay, environment_fingerprint, policy_fingerprint
                )
        for failure in runner_failures:
            if failure.category == "environment" and failure.runner_failure_id not in handled_source_ids:
                return self._runner_failure(
                    run_id, plan, budget, failure, allow_runner_switch
                )
        for verification in verifications:
            if verification.failure_type == "permission_violation" and verification.verification_id not in handled_source_ids:
                return self._permission_regression(run_id, product_id, plan, budget, verification)
        for execution in executions:
            incomplete = (
                execution.status == "completed"
                and (not execution.tool_calls or not execution.output_fingerprint)
            )
            if incomplete and execution.execution_id not in handled_source_ids:
                return self._instrumentation(run_id, plan, budget, execution)
        for metric in metrics:
            if metric.variance > 0 and metric.metrics_id not in handled_source_ids:
                return self._stabilize(run_id, plan, budget, metric)
        return None

    @staticmethod
    def _after_plan(plan: EvalPlan, item: EvalPlanItem) -> EvalPlan:
        return EvalPlan(
            product_id=plan.product_id,
            changeset_id=plan.changeset_id,
            items=[*plan.items, item],
        )

    @staticmethod
    def _work_item(run_id: str, plan: EvalPlan, case_id: str, objective: str, acceptance: str, *, environment_capture: bool = False) -> WorkItem:
        return WorkItem(
            harness_run_id=run_id,
            eval_case_id=case_id,
            objective=objective,
            input_artifact_ids=[plan.eval_plan_id],
            expected_output_type="environment_capture" if environment_capture else "execution_result",
            acceptance_criteria=acceptance,
            allowed_tools=[] if environment_capture else ["read_file", "write_file", "delete_file"],
        )

    def _instrumentation(self, run_id: str, plan: EvalPlan, budget: ReplanBudget, execution: ExecutionResult) -> ReplanOutcome:
        item = self._work_item(
            run_id, plan, "eval_replan_instrumentation",
            "Re-run the affected evaluation with normalized Trace instrumentation.",
            "Persist non-empty tool calls and an output fingerprint in the normalized Trace.",
        )
        after = self._after_plan(plan, EvalPlanItem(
            eval_case_id=item.eval_case_id, selected=True, reason="Trace was incomplete.", risk="medium", oracle_kind="tool_trace"
        ))
        return self._outcome(run_id, "incomplete_trace", plan, after, budget, [item], [execution.execution_id])

    def _stabilize(self, run_id: str, plan: EvalPlan, budget: ReplanBudget, metric: TrialMetrics) -> ReplanOutcome:
        if budget.additional_trial_used >= budget.additional_trial_limit:
            return self._outcome(run_id, "unstable_results", plan, plan, budget, [], [metric.metrics_id], terminal="budget_exhausted")
        item = self._work_item(
            run_id, plan, "eval_replan_stability",
            "Run one additional fixed-environment trial to resolve an unstable result.",
            "Use the saved candidate, policy, seed discipline, and local isolated runner.",
        )
        after = self._after_plan(plan, EvalPlanItem(
            eval_case_id=item.eval_case_id, selected=True, reason="Persisted trial variance is non-zero.", risk="medium", oracle_kind="state_assertion"
        ))
        used = budget.model_copy(update={"additional_trial_used": budget.additional_trial_used + 1})
        return self._outcome(run_id, "unstable_results", plan, after, budget, [item], [metric.metrics_id], after_budget=used)

    def _permission_regression(self, run_id: str, product_id: str, plan: EvalPlan, budget: ReplanBudget, verification: VerificationResult) -> ReplanOutcome:
        case = EvalCase(
            product_id=product_id,
            name="Replan safety regression case",
            oracle_kind="path_policy",
        )
        item = self._work_item(
            run_id, plan, case.eval_case_id,
            "Execute an additional safety EvalCase after a permission regression.",
            "Verify that the policy blocks the unauthorized side effect in an isolated sandbox.",
        )
        after = self._after_plan(plan, EvalPlanItem(
            eval_case_id=case.eval_case_id, selected=True, reason="Permission regression requires a safety EvalCase.", risk="critical", oracle_kind=case.oracle_kind
        ))
        outcome = self._outcome(run_id, "permission_regression", plan, after, budget, [item], [verification.verification_id], risk_escalated=True)
        return ReplanOutcome(outcome.record, outcome.work_items, [case])

    def _runner_failure(self, run_id: str, plan: EvalPlan, budget: ReplanBudget, failure: RunnerFailure, allow_runner_switch: bool) -> ReplanOutcome:
        if not allow_runner_switch:
            return self._outcome(run_id, "runner_environment_failure", plan, plan, budget, [], [failure.runner_failure_id], terminal="runner_blocked", risk_escalated=True)
        item = self._work_item(
            run_id, plan, "eval_replan_local_runner",
            "Repeat the affected bounded evaluation with the local isolated Runner.",
            "Persist a local normalized trace and retain the original external failure as evidence.",
        )
        after = self._after_plan(plan, EvalPlanItem(
            eval_case_id=item.eval_case_id, selected=True, reason="External Runner environment failed; local switch explicitly authorized.", risk="high", oracle_kind="tool_trace"
        ))
        outcome = self._outcome(run_id, "runner_environment_failure", plan, after, budget, [item], [failure.runner_failure_id], risk_escalated=True)
        return ReplanOutcome(outcome.record, outcome.work_items, [], execute_with_local_runner=True)

    def _replay_unresolved(
        self, run_id: str, product_id: str, plan: EvalPlan, budget: ReplanBudget, replay: ReplayResult,
        environment_fingerprint: str, policy_fingerprint: str,
    ) -> ReplanOutcome:
        case = EvalCase(product_id=product_id, name="Replan environment capture", oracle_kind="tool_trace")
        item = self._work_item(
            run_id, plan, case.eval_case_id,
            "Capture the replay environment after a non-reproduced replay.",
            "Persist runner, policy, and environment fingerprints; do not claim reproduction.",
            environment_capture=True,
        )
        after = self._after_plan(plan, EvalPlanItem(
            eval_case_id=case.eval_case_id, selected=True, reason="Replay did not reproduce; environment capture is required.", risk="high", oracle_kind=case.oracle_kind
        ))
        outcome = self._outcome(run_id, "replay_not_reproduced", plan, after, budget, [item], [replay.replay_result_id], terminal="unresolved")
        capture = EnvironmentCapture(
            harness_run_id=run_id, replan_id=outcome.record.replan_id,
            environment_fingerprint=environment_fingerprint, policy_fingerprint=policy_fingerprint,
            runner_ref="local-file-runner", reason="Replay did not reproduce the saved trace.",
        )
        return ReplanOutcome(outcome.record, outcome.work_items, [case], capture)

    @staticmethod
    def _outcome(
        run_id: str, trigger: str, before: EvalPlan, after: EvalPlan, budget: ReplanBudget,
        items: list[WorkItem], sources: list[str], *, after_budget: ReplanBudget | None = None,
        risk_escalated: bool = False, terminal: str = "applied",
    ) -> ReplanOutcome:
        record = ReplanRecord(
            harness_run_id=run_id, trigger=trigger, before_plan=before, after_plan=after,
            added_work_item_ids=[item.work_item_id for item in items], source_artifact_ids=sources,
            budget_before=budget, budget_after=after_budget or budget,
            risk_escalated=risk_escalated, terminal_reason=terminal,
        )
        return ReplanOutcome(record, items, [])
