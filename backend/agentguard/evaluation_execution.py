"""Execution-to-plan bindings for scenario-aware evaluation evidence.

The execution layer owns the relationship between a persisted trial and the
scenario from the immutable Evaluation Plan that it actually ran.  Adapters
consume the validated mapping; Analysts never infer it from prompts or trial
names.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .evaluation_planning import EvaluationPlan


class EvaluationExecutionError(ValueError):
    """Raised when execution evidence cannot be bound to an Evaluation Plan."""


class TrialScenarioBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trial_ref: str = Field(min_length=1, max_length=200)
    scenario_id: str = Field(min_length=1, max_length=100)


class EvaluationExecutionMapping(BaseModel):
    """Immutable, plan-scoped mapping emitted beside execution artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-execution-map.v1"] = "aig.evaluation-execution-map.v1"
    evaluation_plan_id: str = Field(min_length=1)
    bindings: list[TrialScenarioBinding] = Field(min_length=1, max_length=200)

    def by_trial_ref(self) -> dict[str, str]:
        return {binding.trial_ref: binding.scenario_id for binding in self.bindings}


def build_evaluation_execution_mapping(
    plan: EvaluationPlan,
    trial_refs: Sequence[str],
    scenario_ids_by_trial_ref: Mapping[str, str],
) -> EvaluationExecutionMapping:
    """Validate a complete execution binding against one immutable plan."""

    expected_trials = _unique_nonempty(trial_refs, "trial_ref")
    actual_keys = set(scenario_ids_by_trial_ref)
    expected_keys = set(expected_trials)
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    if missing or extra:
        raise EvaluationExecutionError(
            f"Execution scenario mapping must cover exactly the supplied trials (missing={missing}, extra={extra})."
        )

    planned_ids = {scenario.scenario_id for scenario in plan.scenarios}
    invalid = sorted(
        (trial_ref, scenario_id)
        for trial_ref, scenario_id in scenario_ids_by_trial_ref.items()
        if not isinstance(scenario_id, str) or scenario_id not in planned_ids
    )
    if invalid:
        raise EvaluationExecutionError(
            f"Execution scenario mapping contains IDs outside the Evaluation Plan: {invalid}."
        )

    return EvaluationExecutionMapping(
        evaluation_plan_id=plan.plan_id,
        bindings=[
            TrialScenarioBinding(trial_ref=trial_ref, scenario_id=scenario_ids_by_trial_ref[trial_ref])
            for trial_ref in expected_trials
        ],
    )


def parse_execution_scenario_mapping(payload: object) -> dict[str, str]:
    """Parse the small CLI input shape without trusting it as evidence."""

    if not isinstance(payload, Mapping):
        raise EvaluationExecutionError("Scenario mapping input must be a JSON object.")
    raw = payload.get("scenario_ids_by_trial_ref", payload)
    if not isinstance(raw, Mapping) or not raw:
        raise EvaluationExecutionError(
            "Scenario mapping input must contain a non-empty scenario_ids_by_trial_ref object."
        )
    mapping: dict[str, str] = {}
    for trial_ref, scenario_id in raw.items():
        if not isinstance(trial_ref, str) or not trial_ref.strip():
            raise EvaluationExecutionError("Scenario mapping contains an invalid trial_ref.")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise EvaluationExecutionError(f"Scenario mapping for {trial_ref!r} has an invalid scenario_id.")
        mapping[trial_ref] = scenario_id
    return mapping


def _unique_nonempty(values: Sequence[str], label: str) -> list[str]:
    result = list(values)
    if not result or any(not isinstance(value, str) or not value.strip() for value in result):
        raise EvaluationExecutionError(f"Execution mapping requires non-empty {label} values.")
    if len(result) != len(set(result)):
        raise EvaluationExecutionError(f"Execution mapping received duplicate {label} values.")
    return result


__all__ = [
    "EvaluationExecutionError",
    "EvaluationExecutionMapping",
    "TrialScenarioBinding",
    "build_evaluation_execution_mapping",
    "parse_execution_scenario_mapping",
]
