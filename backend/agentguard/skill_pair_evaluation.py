"""Project-agnostic Skill Pair target and change translation."""

from __future__ import annotations

from .evaluation_planning import EvaluationChange, EvaluationTarget
from .evaluation_request import EvaluationRequest
from .project_intelligence import ProjectIntelligence
from .semantic_reporting import ProductDefinition


def build_skill_pair_evaluation_target(
    intelligence: ProjectIntelligence,
    pair_name: str,
    product_definition: ProductDefinition,
) -> EvaluationTarget:
    """Resolve a registered pair into the common Planner target model."""

    pair = next(
        (
            item
            for item in intelligence.capability_registry
            if item.component_type == "skill_pair" and item.name == pair_name
        ),
        None,
    )
    if pair is None:
        raise ValueError(f"Skill Pair {pair_name} is not registered for project {intelligence.project_id}.")
    if product_definition.component_type != "skill_pair" or product_definition.component_name != pair_name:
        raise ValueError("Skill Pair product definition and registry component do not match.")
    if len(pair.dependencies) != 2:
        raise ValueError("Registered Skill Pair must resolve to exactly two Skill members.")
    member_by_name = {item.name: item for item in intelligence.capability_registry}
    member_context = [
        {
            "name": member,
            "description": intelligence.agent_manifest.capability_descriptions.get(member, ""),
            "responsibility": member_by_name[member].responsibility,
            "dependencies": list(member_by_name[member].dependencies),
            "boundary": list(member_by_name[member].boundary),
        }
        for member in pair.dependencies
        if member in member_by_name
    ]
    return EvaluationTarget(
        target_id=pair.capability_id,
        project_id=intelligence.project_id,
        component_type="skill_pair",
        name=pair.name,
        description=product_definition.description or pair.responsibility,
        product_responsibility=product_definition.product_responsibility,
        user_job=product_definition.user_job,
        expected_behavior=product_definition.expected_behavior,
        quality_dimensions=product_definition.quality_dimensions,
        boundary=product_definition.boundary,
        definition_status=product_definition.definition_status,
        evidence_refs=product_definition.evidence_refs,
        component_members=list(pair.dependencies),
        component_member_context=member_context,
        trace_event_types=list(intelligence.runtime_profile.trace_event_types),
        fixture_catalog=intelligence.runtime_profile.fixture_catalog,
    )


def build_skill_pair_evaluation_change(
    request: EvaluationRequest,
    *,
    evaluation_name: str,
) -> EvaluationChange:
    if request.component_type != "skill_pair":
        raise ValueError("Skill Pair change translation requires component_type=skill_pair.")
    return EvaluationChange(
        change_id=request.request_id,
        project_id=request.project_id,
        change_type="interaction",
        evaluation_type="skill_pair_evaluation",
        evaluation_name=evaluation_name,
        summary=f"Evaluate the interaction value and risk of {request.component_name}.",
        baseline_ref=request.baseline_version,
        candidate_ref=request.candidate_version,
    )


def skill_pair_experiment_ids_by_condition(plan) -> dict[str, str]:
    if plan.component_type != "skill_pair" or plan.evaluation_type != "skill_pair_evaluation":
        raise ValueError("Skill Pair condition mapping requires a Skill Pair Evaluation Plan.")
    return {
        "a_only": plan.experiment_for_kind("pair_a_only").experiment_id,
        "b_only": plan.experiment_for_kind("pair_b_only").experiment_id,
        "combined": plan.experiment_for_kind("pair_combined").experiment_id,
    }


__all__ = [
    "build_skill_pair_evaluation_change",
    "build_skill_pair_evaluation_target",
    "skill_pair_experiment_ids_by_condition",
]
