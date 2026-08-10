"""Project-neutral execution of an Interaction Evaluation matrix.

The executor owns matrix completeness and evidence shape. A target adapter
owns only how one declared scenario/condition is run and how its independent
Oracle is evaluated. Project-specific execution details stay outside the
AgentGuard execution core.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evaluation_planning import EvaluationPlan, EvaluationScenario, scenario_hash_for
from .evaluation_suite import build_evaluation_suite_aggregate
from .scenario_contracts import EvaluationReadinessResult


EvaluationConditionKind = Literal[
    "a_only",
    "b_only",
    "combined",
    "baseline",
    "removal",
    "replacement",
]
ConditionKind = EvaluationConditionKind
PAIR_INTERACTION_CONDITIONS: tuple[ConditionKind, ...] = ("a_only", "b_only", "combined")


class InteractionExecutionError(ValueError):
    """Raised when a matrix cannot produce a complete immutable artifact."""


class InteractionTrialResult(BaseModel):
    """One adapter-produced trial before it is placed in the matrix artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1, max_length=100)
    repetition_id: str | None = Field(default=None, min_length=1, max_length=160)
    repetition_index: int = Field(default=1, ge=1, le=10)
    condition_kind: ConditionKind
    category: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=240)
    observations: dict[str, object] = Field(default_factory=dict)
    trace: list[dict[str, object]] = Field(min_length=1, max_length=10000)
    output: object
    metrics: dict[str, int | float | str] = Field(min_length=2)
    oracle: dict[str, object]
    evidence_refs: list[str] = Field(min_length=1, max_length=100)
    provider_request_ids: list[str] = Field(default_factory=list, max_length=32)
    usage: dict[str, int | float | str] = Field(default_factory=dict)
    output_artifact_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> "InteractionTrialResult":
        if any(not isinstance(item.get("event_type"), str) or not str(item["event_type"]).strip() for item in self.trace):
            raise ValueError("Interaction trial trace events require event_type.")
        for key in ("latency_ms", "cost_usd"):
            value = self.metrics.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"Interaction trial metrics require non-negative {key}.")
        if self.oracle.get("status") != "verified":
            raise ValueError("Interaction trial requires a verified independent Oracle.")
        if self.oracle.get("oracle_type") not in {"rule_based", "frozen_lookup", "structured_state"}:
            raise ValueError("Interaction trial requires a deterministic Oracle type.")
        if not isinstance(self.oracle.get("oracle_version"), str) or not self.oracle["oracle_version"]:
            raise ValueError("Interaction trial requires an Oracle version.")
        if not isinstance(self.oracle.get("validation_input"), dict) or not self.oracle["validation_input"]:
            raise ValueError("Interaction trial requires recorded Oracle validation input.")
        oracle_refs = self.oracle.get("evidence_refs")
        if not isinstance(oracle_refs, list) or not oracle_refs or any(not isinstance(ref, str) or not ref for ref in oracle_refs):
            raise ValueError("Interaction trial Oracle requires evidence_refs.")
        return self


class InteractionTrialRunner(Protocol):
    """Target adapter boundary for one scenario and one matrix condition."""

    def run(
        self,
        scenario: EvaluationScenario,
        condition_kind: ConditionKind,
        *,
        trial_root: Path,
    ) -> InteractionTrialResult: ...


class EvaluationMatrixArtifact(BaseModel):
    """Type-neutral scenario matrix artifact used by Skill and future Tool runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-matrix-artifact.v1"] = "aig.evaluation-matrix-artifact.v1"
    evaluation_id: str = Field(min_length=1)
    evaluation_type: str = Field(min_length=1)
    evaluation_name: str = Field(min_length=1)
    scope_id: str | None = Field(default=None, min_length=16)
    evaluation_plan_id: str = Field(min_length=1)
    scenario_readiness: EvaluationReadinessResult
    interaction_hypothesis: dict[str, object] | None = None
    scenario_suite: dict[str, object] | None = None
    suite_aggregate: dict[str, object] | None = None
    scenarios: list[dict[str, object]] = Field(min_length=1, max_length=200)
    condition_kinds: list[str] = Field(min_length=1, max_length=8)
    conditions: list[dict[str, object]] = Field(min_length=1, max_length=1000)
    metrics: dict[str, int | float | str]
    evidence_refs: list[str] = Field(min_length=1)
    integrity: dict[str, object]
    artifact_manifest_hash: str = Field(min_length=16)


def execute_evaluation_matrix(
    plan: EvaluationPlan,
    *,
    evaluation_name: str,
    evaluation_id: str,
    readiness: EvaluationReadinessResult,
    runner: InteractionTrialRunner,
    condition_kinds: Sequence[EvaluationConditionKind],
    run_root: Path,
    output_path: Path | None = None,
) -> EvaluationMatrixArtifact:
    """Run every frozen scenario under an explicit condition set."""

    if readiness.evaluation_plan_id != plan.plan_id:
        raise InteractionExecutionError("Scenario Readiness result does not match the Evaluation Plan.")
    if readiness.status != "ready":
        raise InteractionExecutionError(
            "Interaction matrix cannot start because Scenario Readiness is blocked: "
            + "; ".join(readiness.blocking_reasons)
        )
    condition_kinds = tuple(condition_kinds)
    if not condition_kinds or len(set(condition_kinds)) != len(condition_kinds):
        raise InteractionExecutionError("Evaluation matrix requires a non-empty unique condition set.")
    if any(not isinstance(condition, str) or not condition.strip() for condition in condition_kinds):
        raise InteractionExecutionError("Evaluation matrix conditions must be non-empty strings.")
    _validate_frozen_evaluation_plan(plan)
    if plan.evaluation_type in {"skill_pair_evaluation", "tool_skill_interaction"}:
        _validate_frozen_interaction_plan(plan)
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    conditions: list[dict[str, object]] = []
    expected_keys = {
        (scenario.scenario_id, condition_kind, repetition_index)
        for scenario in plan.scenarios
        for condition_kind in condition_kinds
        for repetition_index in range(1, scenario.repetition_count + 1)
    }
    if plan.scenario_suite is not None and len(expected_keys) > plan.scenario_suite.max_trials:
        raise InteractionExecutionError(
            f"Frozen matrix requires {len(expected_keys)} trials, exceeding max_trials={plan.scenario_suite.max_trials}."
        )

    for scenario in plan.scenarios:
        for condition_kind in condition_kinds:
            for repetition_index in range(1, scenario.repetition_count + 1):
                repetition_id = f"{scenario.scenario_id}:{condition_kind}:r{repetition_index}"
                trial_root = run_root / scenario.scenario_id / condition_kind / f"repetition_{repetition_index}"
                trial_root.mkdir(parents=True, exist_ok=True)
                try:
                    result = InteractionTrialResult.model_validate(
                        runner.run(scenario, condition_kind, trial_root=trial_root)
                    ).model_copy(update={
                        "repetition_id": repetition_id,
                        "repetition_index": repetition_index,
                    })
                except Exception as error:
                    raise InteractionExecutionError(
                        f"Interaction trial failed before evidence completion: "
                        f"scenario={scenario.scenario_id}; condition={condition_kind}; "
                        f"repetition={repetition_index}; error={error}"
                    ) from error
                if result.scenario_id != scenario.scenario_id or result.condition_kind != condition_kind:
                    raise InteractionExecutionError("Interaction trial result identity does not match the requested matrix cell.")
                if result.category != scenario.category:
                    raise InteractionExecutionError("Interaction trial category does not match the generated scenario.")
                conditions.append(result.model_dump(mode="json"))

    observed_keys = {
        (item["scenario_id"], item["condition_kind"], item["repetition_index"])
        for item in conditions
    }
    if observed_keys != expected_keys:
        raise InteractionExecutionError("Interaction executor did not produce the exact scenario × condition matrix.")

    total_cost = sum(float(item["metrics"]["cost_usd"]) for item in conditions)
    total_latency = sum(float(item["metrics"]["latency_ms"]) for item in conditions)
    outcomes = [item["oracle"].get("outcome") for item in conditions]
    known_outcomes = {"passed", "failed", "unresolved"}
    outcome_counts = {
        outcome: outcomes.count(outcome)
        for outcome in sorted(known_outcomes)
    }
    failure_rate = (
        (outcome_counts["failed"] + outcome_counts["unresolved"]) / len(conditions)
        if outcomes and all(outcome in known_outcomes for outcome in outcomes)
        else 0.0
    )
    evidence_refs = sorted({ref for item in conditions for ref in item["evidence_refs"]})
    evidence_refs.extend(
        ref for ref in sorted({
            ref
            for item in conditions
            for ref in item["oracle"].get("evidence_refs", [])
            if isinstance(ref, str)
        })
        if ref not in evidence_refs
    )
    scenario_payloads = [scenario.model_dump(mode="json") for scenario in plan.scenarios]
    suite_aggregate = build_evaluation_suite_aggregate(
        scenario_payloads,
        conditions,
        condition_kinds=condition_kinds,
        evaluation_type=plan.evaluation_type,
        suite_config=plan.scenario_suite,
        component_members=plan.component_members,
    )
    unsigned = {
        "schema_version": "aig.evaluation-matrix-artifact.v1",
        "evaluation_id": evaluation_id,
        "evaluation_type": plan.evaluation_type,
        "evaluation_name": evaluation_name,
        "scope_id": plan.evaluation_scope.scope_id if plan.evaluation_scope else None,
        "evaluation_plan_id": plan.plan_id,
        "scenario_readiness": readiness.model_dump(mode="json"),
        "interaction_hypothesis": (
            plan.interaction_hypothesis.model_dump(mode="json")
            if plan.interaction_hypothesis is not None
            else None
        ),
        "scenario_suite": plan.scenario_suite.model_dump(mode="json") if plan.scenario_suite else None,
        "suite_aggregate": suite_aggregate.model_dump(mode="json"),
        "scenarios": scenario_payloads,
        "condition_kinds": list(condition_kinds),
        "conditions": conditions,
        "metrics": {
            "scenario_count": len(plan.scenarios),
            "planned_repetition_count": sum(item.repetition_count for item in plan.scenarios),
            "executed_repetition_count": sum(item.repetition_count for item in plan.scenarios),
            "condition_count": len(conditions),
            "expected_condition_count": len(expected_keys),
            "verified_condition_count": len(conditions),
            "failure_rate": failure_rate,
            "passed_condition_count": outcome_counts["passed"],
            "failed_condition_count": outcome_counts["failed"],
            "unresolved_condition_count": outcome_counts["unresolved"],
            "total_cost_usd": total_cost,
            "total_latency_ms": total_latency,
        },
        "evidence_refs": evidence_refs,
        "integrity": {"status": "complete", "missing": [], "conflicts": []},
    }
    manifest_hash = "sha256:" + hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    artifact = EvaluationMatrixArtifact(
        **unsigned,
        artifact_manifest_hash=manifest_hash,
    )
    if output_path is not None:
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def _validate_frozen_evaluation_plan(plan: EvaluationPlan) -> None:
    """Require every generated scenario to remain bound to its frozen content."""

    for scenario in plan.scenarios:
        if not scenario.scenario_hash or scenario_hash_for(scenario.model_dump(mode="json")) != scenario.scenario_hash:
            raise InteractionExecutionError(
                f"Evaluation scenario is not frozen to its content: {scenario.scenario_id}."
            )
        provenance = scenario.scenario_provenance
        if provenance is None or provenance.frozen is not True:
            raise InteractionExecutionError(
                f"Evaluation scenario has no frozen provenance: {scenario.scenario_id}."
            )
        if provenance.scenario_hash != scenario.scenario_hash:
            raise InteractionExecutionError(
                f"Evaluation scenario provenance hash mismatch: {scenario.scenario_id}."
            )


def _validate_frozen_interaction_plan(plan: EvaluationPlan) -> None:
    """Prevent execution from bypassing the frozen planning provenance contract."""

    if plan.evaluation_type not in {"skill_pair_evaluation", "tool_skill_interaction"}:
        return
    hypothesis = plan.interaction_hypothesis
    if hypothesis is None or not hypothesis.hypothesis_hash:
        raise InteractionExecutionError(
            "Interaction execution requires a hashed Eval Engineering relationship hypothesis."
        )
    for scenario in plan.scenarios:
        if not scenario.scenario_hash or scenario_hash_for(scenario.model_dump(mode="json")) != scenario.scenario_hash:
            raise InteractionExecutionError(
                f"Interaction scenario is not frozen to its content: {scenario.scenario_id}."
            )
        provenance = scenario.scenario_provenance
        if provenance is None or provenance.frozen is not True:
            raise InteractionExecutionError(
                f"Interaction scenario has no frozen provenance: {scenario.scenario_id}."
            )
        if provenance.scenario_hash != scenario.scenario_hash:
            raise InteractionExecutionError(
                f"Interaction scenario provenance hash mismatch: {scenario.scenario_id}."
            )
        if provenance.relationship_hypothesis_hash != hypothesis.hypothesis_hash:
            raise InteractionExecutionError(
                f"Interaction scenario is not bound to the relationship hypothesis: {scenario.scenario_id}."
            )


__all__ = [
    "ConditionKind",
    "EvaluationConditionKind",
    "InteractionExecutionError",
    "EvaluationMatrixArtifact",
    "InteractionTrialResult",
    "InteractionTrialRunner",
    "PAIR_INTERACTION_CONDITIONS",
    "execute_evaluation_matrix",
]
