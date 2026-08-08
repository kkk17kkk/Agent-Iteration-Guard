"""Unified Evaluation request dispatch for planning.

The API and CLI must not know how a component-specific target or change is
constructed.  This module is the single boundary from the persisted
EvaluationRequest to the registered Planner Strategy.  It deliberately does
not execute a target or manufacture evidence.
"""

from __future__ import annotations

from typing import Literal, Sequence

from .evaluation_memory import EvaluationKnowledge
from .evaluation_planning import (
    EvaluationChange,
    EvaluationPlan,
    EvaluationTarget,
    PlannerStrategyError,
    PlannerStrategyRegistry,
    build_evolution_evaluation_plan,
    default_planner_strategy_registry,
)
from .evaluation_request import EvaluationRequest
from .evaluation_scenario_generator import EvaluationEvidenceRequirementsGenerator, EvaluationScenarioGenerator
from .project_intelligence import CapabilityRecord, ProjectIntelligence
from .semantic_reporting import ProductDefinition
from .skill_ablation_adapter import build_skill_evaluation_change
from .skill_pair_evaluation import build_skill_pair_evaluation_change, build_skill_pair_evaluation_target


DispatchStatus = Literal["unsupported", "deferred"]


class EvaluationDispatchError(ValueError):
    """A stable error for an unavailable component evaluation strategy."""

    def __init__(self, code: str, status: DispatchStatus, message: str) -> None:
        self.code = code
        self.status = status
        super().__init__(f"{code}: {message}")


def build_evaluation_target(
    intelligence: ProjectIntelligence,
    request: EvaluationRequest,
    product_definition: ProductDefinition,
) -> EvaluationTarget:
    """Resolve a request into a common target without importing evidence."""

    if request.project_id != intelligence.project_id:
        raise ValueError("EvaluationRequest and ProjectIntelligence must belong to the same project.")
    if product_definition.component_type != request.component_type:
        raise ValueError("Product definition component_type does not match the EvaluationRequest.")
    if product_definition.component_name != request.component_name:
        raise ValueError("Product definition component_name does not match the EvaluationRequest.")

    if request.component_type == "skill_pair":
        return build_skill_pair_evaluation_target(
            intelligence,
            request.component_name,
            product_definition,
            request.pair_members,
        )
    if request.component_type == "tool":
        raise EvaluationDispatchError(
            "E_TOOL_PLANNER_DEFERRED",
            "deferred",
            "Tool evaluation has an evidence adapter but no registered executable Planner Strategy yet.",
        )

    capability = _capability_for_request(intelligence, request)
    if not capability.capability_id:
        raise ValueError(f"Registered Skill {request.component_name!r} has no stable capability_id.")
    runtime = intelligence.runtime_profile
    return EvaluationTarget(
        target_id=capability.capability_id,
        project_id=request.project_id,
        component_type="skill",
        name=capability.name,
        component_pattern=capability.name,
        description=product_definition.description,
        product_responsibility=product_definition.product_responsibility,
        user_job=product_definition.user_job,
        expected_behavior=product_definition.expected_behavior or [capability.responsibility],
        quality_dimensions=product_definition.quality_dimensions,
        boundary=product_definition.boundary or capability.boundary,
        definition_status=product_definition.definition_status,
        source_ref=runtime.source_ref,
        evidence_refs=product_definition.evidence_refs or capability.source_refs,
        trace_event_types=list(runtime.trace_event_types),
        fixture_catalog=runtime.fixture_catalog,
    )


def build_evaluation_change(request: EvaluationRequest, *, evaluation_name: str) -> EvaluationChange:
    """Translate one request to the change vocabulary consumed by the registry."""

    if request.component_type == "skill":
        return build_skill_evaluation_change(request, evaluation_name=evaluation_name)
    if request.component_type == "skill_pair":
        return build_skill_pair_evaluation_change(request, evaluation_name=evaluation_name)
    raise EvaluationDispatchError(
        "E_TOOL_PLANNER_DEFERRED",
        "deferred",
        "Tool evaluation has an evidence adapter but no registered executable Planner Strategy yet.",
    )


def build_evaluation_plan_for_request(
    request: EvaluationRequest,
    intelligence: ProjectIntelligence,
    product_definition: ProductDefinition,
    *,
    evaluation_name: str,
    scenario_generator: EvaluationScenarioGenerator,
    evidence_requirements_generator: EvaluationEvidenceRequirementsGenerator,
    knowledge_pattern: str | None = None,
    evaluation_knowledge: Sequence[EvaluationKnowledge] = (),
    registry: PlannerStrategyRegistry | None = None,
) -> EvaluationPlan:
    """Build a plan through one component-neutral Planner dispatch path."""

    target = build_evaluation_target(intelligence, request, product_definition).model_copy(update={
        "component_pattern": knowledge_pattern or request.component_type,
        "evaluation_knowledge": list(evaluation_knowledge),
    })
    change = build_evaluation_change(request, evaluation_name=evaluation_name)
    planner_registry = registry or default_planner_strategy_registry()
    if not planner_registry.supports(target.component_type, change.change_type):
        raise EvaluationDispatchError(
            "E_PLANNER_STRATEGY_UNSUPPORTED",
            "unsupported",
            f"No executable Planner Strategy is registered for {target.component_type}/{change.change_type}.",
        )
    try:
        return build_evolution_evaluation_plan(
            target,
            change,
            registry=planner_registry,
            scenario_generator=scenario_generator,
            evidence_requirements_generator=evidence_requirements_generator,
        )
    except PlannerStrategyError:
        raise


def _capability_for_request(
    intelligence: ProjectIntelligence,
    request: EvaluationRequest,
) -> CapabilityRecord:
    candidates = list(intelligence.capability_registry)
    for snapshot in intelligence.snapshot_history:
        candidates.extend(snapshot.capability_registry)
    capability = next(
        (
            item
            for item in candidates
            if item.component_type == request.component_type and item.name == request.component_name
        ),
        None,
    )
    if capability is None:
        raise ValueError(
            f"Component {request.component_type}/{request.component_name} is not registered for project {request.project_id}."
        )
    return capability


__all__ = [
    "DispatchStatus",
    "EvaluationDispatchError",
    "build_evaluation_change",
    "build_evaluation_plan_for_request",
    "build_evaluation_target",
]
