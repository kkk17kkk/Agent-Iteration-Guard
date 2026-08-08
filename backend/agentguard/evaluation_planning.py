"""Generic Agent Evolution Evaluation Planning contracts and dispatch."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .evolution_types import (
    ChangeType,
    ComponentType,
    EvaluationDimension,
    EvaluationExperimentKind,
    EvaluationType,
)
from .evaluation_memory import EvaluationKnowledge
from .evaluation_scope import EvaluationScope
from .interaction_evaluation import InteractionRelationshipProfile, PlanningCallMetadata
from .scenario_contracts import FixtureCatalog, ScenarioInputContract


class PlannerStrategyError(ValueError):
    """Raised when no registered strategy can design a requested evaluation."""


class EvaluationTarget(BaseModel):
    """Product-level description of the Agent capability being evaluated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "aig.evaluation-target.v1"
    target_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    component_type: ComponentType
    name: str = Field(min_length=1)
    component_pattern: str | None = Field(default=None, max_length=160)
    description: str = Field(min_length=1)
    product_responsibility: str = Field(min_length=1, max_length=300)
    user_job: str = Field(min_length=1, max_length=300)
    expected_behavior: list[str] = Field(default_factory=list, max_length=8)
    quality_dimensions: list[str] = Field(default_factory=list, max_length=8)
    boundary: list[str] = Field(default_factory=list, max_length=8)
    definition_status: str = Field(default="candidate", min_length=1)
    source_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    component_members: list[str] = Field(default_factory=list, max_length=2)
    component_member_context: list[dict[str, object]] = Field(default_factory=list, max_length=2)
    trace_event_types: list[str] = Field(default_factory=list, max_length=100)
    fixture_catalog: FixtureCatalog = Field(default_factory=FixtureCatalog)
    evaluation_knowledge: list[EvaluationKnowledge] = Field(default_factory=list, max_length=8)


class EvaluationChange(BaseModel):
    """Change-level input used to select and parameterize a planning strategy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "aig.evaluation-change.v1"
    change_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    change_type: ChangeType
    evaluation_type: EvaluationType
    evaluation_name: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=360)
    baseline_ref: str | None = None
    candidate_ref: str | None = None
    related_target_ids: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class EvaluationDimensionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: EvaluationDimension
    question: str = Field(min_length=1, max_length=240)
    success_criteria: list[str] = Field(min_length=1, max_length=4)
    evidence_to_collect: list[str] = Field(min_length=1, max_length=5)


ScenarioCategory = Literal[
    "normal",
    "constraint_conflict",
    "boundary",
    "robustness",
    "interaction",
    "complementary",
    "synergy",
    "conflict",
    "single_skill_dominant",
]


class PairScenarioExpectedBehavior(BaseModel):
    """Product-facing expectations for the two single arms and the pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_a_only: str = Field(min_length=1, max_length=360)
    skill_b_only: str = Field(min_length=1, max_length=360)
    combined: str = Field(min_length=1, max_length=360)


class EvaluationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1, max_length=100)
    category: ScenarioCategory
    user_prompt: str = Field(min_length=1, max_length=600)
    evaluation_goal: str = Field(min_length=1, max_length=300)
    expected_success_behavior: list[str] = Field(min_length=1, max_length=8)
    evidence_to_collect: list[str] = Field(min_length=1, max_length=8)
    expected_behavior: PairScenarioExpectedBehavior | None = None
    input_contract: ScenarioInputContract = Field(default_factory=ScenarioInputContract.no_input)
    scenario_hash: str | None = Field(default=None, min_length=16, max_length=200)
    scenario_provenance: "ScenarioProvenance | None" = None


class ScenarioProvenance(BaseModel):
    """Frozen provenance attached to a generated scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_source: str = Field(min_length=1, max_length=120)
    relationship_hypothesis_hash: str | None = Field(default=None, min_length=16, max_length=200)
    provider_metadata: PlanningCallMetadata
    scenario_hash: str = Field(min_length=16, max_length=200)
    frozen: Literal[True] = True


class EvaluationEvidenceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str = Field(min_length=1, max_length=120)
    scenario_id: str = Field(min_length=1, max_length=100)
    dimensions: list[EvaluationDimension] = Field(min_length=1, max_length=8)
    evidence_to_collect: list[str] = Field(min_length=1, max_length=8)


class EvaluationExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str = Field(min_length=1, max_length=100)
    experiment_kind: EvaluationExperimentKind
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=300)
    design: str = Field(min_length=1, max_length=360)
    control_group: str = Field(min_length=1, max_length=300)
    comparison: str = Field(min_length=1, max_length=300)
    dimensions: list[EvaluationDimension] = Field(min_length=1, max_length=8)
    success_criteria: list[str] = Field(min_length=1, max_length=8)


class EvaluationPlan(BaseModel):
    """Immutable product experiment design shared by every change type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "aig.evaluation-plan.v3"
    plan_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    change_type: ChangeType
    evaluation_type: EvaluationType
    evaluation_name: str = Field(min_length=1)
    component_type: ComponentType
    component_name: str = Field(min_length=1)
    component_members: list[str] = Field(default_factory=list, max_length=2)
    product_responsibility: str = Field(min_length=1, max_length=300)
    user_job: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=360)
    hypothesis: str = Field(min_length=1, max_length=360)
    dimensions: list[EvaluationDimensionPlan] = Field(min_length=4, max_length=8)
    experiments: list[EvaluationExperiment] = Field(min_length=1, max_length=8)
    comparison_question: str = Field(min_length=1, max_length=300)
    scenarios: list[EvaluationScenario] = Field(min_length=3, max_length=5)
    evidence_requirements: list[EvaluationEvidenceRequirement] = Field(min_length=3, max_length=5)
    overall_success_criteria: list[str] = Field(min_length=1, max_length=8)
    interaction_hypothesis: InteractionRelationshipProfile | None = None
    evaluation_knowledge: list[EvaluationKnowledge] = Field(default_factory=list, max_length=8)
    evaluation_scope: EvaluationScope | None = None
    status: str = Field(default="approved", min_length=1)
    planning_method: str = Field(default="eval_engineering", min_length=1)

    def experiment_for_kind(self, kind: EvaluationExperimentKind) -> EvaluationExperiment:
        for experiment in self.experiments:
            if experiment.experiment_kind == kind:
                return experiment
        raise PlannerStrategyError(f"Evaluation Plan has no experiment for {kind}.")


class EvaluationPlanDesign(BaseModel):
    """Strategy output before it is bound to target and change identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rationale: str = Field(min_length=1, max_length=360)
    hypothesis: str = Field(min_length=1, max_length=360)
    dimensions: list[EvaluationDimensionPlan] = Field(min_length=4, max_length=8)
    experiments: list[EvaluationExperiment] = Field(min_length=1, max_length=8)
    comparison_question: str = Field(min_length=1, max_length=300)
    scenarios: list[EvaluationScenario] = Field(min_length=3, max_length=5)
    evidence_requirements: list[EvaluationEvidenceRequirement] = Field(min_length=3, max_length=5)
    overall_success_criteria: list[str] = Field(min_length=1, max_length=8)
    interaction_hypothesis: InteractionRelationshipProfile | None = None


class EvaluationPlanningAssistant(Protocol):
    """Eval Engineering design assistant used by a registered strategy."""

    def design(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        scenario_generator,
        evidence_requirements_generator,
    ) -> EvaluationPlanDesign: ...


class PlannerStrategy(Protocol):
    component_type: ComponentType
    change_type: ChangeType

    def design(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        scenario_generator,
        evidence_requirements_generator,
    ) -> EvaluationPlanDesign: ...


@dataclass(frozen=True)
class RegisteredPlannerStrategy:
    component_type: ComponentType
    change_type: ChangeType
    strategy: PlannerStrategy


class PlannerStrategyRegistry:
    """Selects experiment design by abstract component and change type."""

    def __init__(self) -> None:
        self._strategies: dict[tuple[ComponentType, ChangeType], RegisteredPlannerStrategy] = {}

    def register(self, strategy: PlannerStrategy) -> None:
        key = (strategy.component_type, strategy.change_type)
        if key in self._strategies:
            raise PlannerStrategyError(f"A planner strategy is already registered for {key}.")
        self._strategies[key] = RegisteredPlannerStrategy(*key, strategy)

    def supports(self, component_type: ComponentType, change_type: ChangeType) -> bool:
        return (component_type, change_type) in self._strategies

    def strategy_for(self, component_type: ComponentType, change_type: ChangeType) -> PlannerStrategy:
        try:
            return self._strategies[(component_type, change_type)].strategy
        except KeyError as error:
            raise PlannerStrategyError(
                f"No Evaluation Planner strategy is registered for {component_type}/{change_type}."
            ) from error


def default_planner_strategy_registry() -> PlannerStrategyRegistry:
    """Return the currently installed strategy set without hiding future types."""

    from .eval_engineering_skill import EvalEngineeringAblationStrategy, EvalEngineeringSkillPairStrategy

    registry = PlannerStrategyRegistry()
    registry.register(EvalEngineeringAblationStrategy())
    registry.register(EvalEngineeringSkillPairStrategy())
    return registry


def build_evolution_plan(
    target: EvaluationTarget,
    change: EvaluationChange,
    *,
    registry: PlannerStrategyRegistry | None = None,
    scenario_generator=None,
    evidence_requirements_generator=None,
    evaluation_scope: EvaluationScope | None = None,
) -> EvaluationPlan:
    """Build one generic Evaluation Plan through the registered strategy."""

    if target.project_id != change.project_id:
        raise PlannerStrategyError("Evaluation Target and Change must belong to the same project.")
    strategy = (registry or default_planner_strategy_registry()).strategy_for(
        target.component_type, change.change_type
    )
    if strategy.component_type != target.component_type or strategy.change_type != change.change_type:
        raise PlannerStrategyError("Planner strategy identity does not match the requested target change.")
    if scenario_generator is None or evidence_requirements_generator is None:
        raise PlannerStrategyError(
            "Evolution Planner requires an Evaluation Scenario Generator and Evidence Requirements Generator."
        )
    design = strategy.design(
        target,
        change,
        scenario_generator=scenario_generator,
        evidence_requirements_generator=evidence_requirements_generator,
    )
    return EvaluationPlan(
        plan_id=_plan_id(target, change, design, evaluation_scope),
        project_id=target.project_id,
        target_id=target.target_id,
        change_id=change.change_id,
        change_type=change.change_type,
        evaluation_type=change.evaluation_type,
        evaluation_name=change.evaluation_name,
        component_type=target.component_type,
        component_name=target.name,
        component_members=list(target.component_members),
        product_responsibility=target.product_responsibility,
        user_job=target.user_job,
        rationale=design.rationale,
        hypothesis=design.hypothesis,
        dimensions=design.dimensions,
        experiments=design.experiments,
        comparison_question=design.comparison_question,
        scenarios=design.scenarios,
        evidence_requirements=design.evidence_requirements,
        overall_success_criteria=design.overall_success_criteria,
        interaction_hypothesis=design.interaction_hypothesis,
        evaluation_knowledge=list(target.evaluation_knowledge),
        evaluation_scope=evaluation_scope,
    )


def build_evolution_evaluation_plan(
    target: EvaluationTarget,
    change: EvaluationChange,
    *,
    registry: PlannerStrategyRegistry | None = None,
    scenario_generator=None,
    evidence_requirements_generator=None,
    evaluation_scope: EvaluationScope | None = None,
) -> EvaluationPlan:
    """Canonical generic Planner entry point for all Agent Evolution changes."""

    return build_evolution_plan(
        target,
        change,
        registry=registry,
        scenario_generator=scenario_generator,
        evidence_requirements_generator=evidence_requirements_generator,
        evaluation_scope=evaluation_scope,
    )


def bind_evaluation_scope(plan: EvaluationPlan, scope: EvaluationScope) -> EvaluationPlan:
    """Bind a frozen runtime Scope after scenarios have been generated once."""

    if plan.project_id != scope.project_id or plan.change_id != scope.evaluation_request_id:
        raise PlannerStrategyError("Evaluation Scope does not match the Evaluation Plan identity.")
    payload = plan.model_dump(mode="json")
    payload["evaluation_scope"] = scope.model_dump(mode="json")
    payload.pop("plan_id", None)
    plan_id = "plan_" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return plan.model_copy(update={"plan_id": plan_id, "evaluation_scope": scope})


def _plan_id(
    target: EvaluationTarget,
    change: EvaluationChange,
    design: EvaluationPlanDesign,
    evaluation_scope: EvaluationScope | None = None,
) -> str:
    raw = json.dumps(
        {
            "project_id": target.project_id,
            "target_id": target.target_id,
            "change_id": change.change_id,
            "evaluation_name": change.evaluation_name,
            "scenarios": [item.model_dump(mode="json") for item in design.scenarios],
            "evaluation_knowledge": [item.model_dump(mode="json") for item in target.evaluation_knowledge],
            "evaluation_scope": evaluation_scope.model_dump(mode="json") if evaluation_scope else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "plan_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def scenario_hash_for(value: Mapping[str, object]) -> str:
    """Return the canonical hash of a generated scenario before provenance is attached."""

    payload = {
        key: item
        for key, item in value.items()
        if key not in {"scenario_hash", "scenario_provenance"}
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ChangeType",
    "ComponentType",
    "EvaluationChange",
    "EvaluationDimensionPlan",
    "EvaluationEvidenceRequirement",
    "EvaluationExperiment",
    "EvaluationExperimentKind",
    "EvaluationPlan",
    "EvaluationPlanDesign",
    "EvaluationPlanningAssistant",
    "EvaluationTarget",
    "EvaluationScenario",
    "PairScenarioExpectedBehavior",
    "EvaluationType",
    "PlannerStrategy",
    "PlannerStrategyError",
    "PlannerStrategyRegistry",
    "RegisteredPlannerStrategy",
    "ScenarioCategory",
    "ScenarioProvenance",
    "scenario_hash_for",
    "build_evolution_plan",
    "build_evolution_evaluation_plan",
    "bind_evaluation_scope",
    "default_planner_strategy_registry",
]
