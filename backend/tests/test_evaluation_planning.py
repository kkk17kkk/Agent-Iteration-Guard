import json

import pytest

from agentguard.evaluation_planning import (
    EvaluationChange,
    EvaluationScenario,
    EvaluationPlanDesign,
    EvaluationTarget,
    PairScenarioExpectedBehavior,
    PlannerStrategyError,
    PlannerStrategyRegistry,
    build_evolution_plan,
)
from agentguard.evaluation_memory import EvaluationKnowledge
from agentguard.eval_engineering_skill import EvalEngineeringDesignAssistant
from agentguard.evaluation_scenario_generator import ScenarioEvidenceRequirementsGenerator
from agentguard.interaction_evaluation import InteractionRelationshipProfile
from agentguard.evaluation_suite import scenario_category_sequence
from agentguard.evaluation_suite import default_scenario_suite_config


class FakeScenarioGenerator:
    def generate(self, target, change):
        categories = scenario_category_sequence(
            ("normal", "constraint_conflict", "boundary", "robustness"),
            change.scenario_suite,
        )
        return [
            EvaluationScenario(
                scenario_id=f"scenario_{index}",
                category=category,
                user_prompt=f"用户请求测试场景 {index}。",
                evaluation_goal=f"测试 {category}。",
                expected_success_behavior=["完成用户任务"],
                evidence_to_collect=["用户任务结果"],
            )
            for index, category in enumerate(categories, 1)
        ]


class FakePairScenarioGenerator(FakeScenarioGenerator):
    def analyze_pair_relationship(self, target, change):
        return InteractionRelationshipProfile(
            relationship="complementary",
            rationale="The second capability validates and improves the first output.",
            signals=["shared user job", "information handoff"],
        )

    def generate_pair_scenarios(self, target, change, *, relationship):
        categories = scenario_category_sequence(
            ("complementary", "synergy", "conflict", "boundary"),
            change.scenario_suite,
        )
        return [
            EvaluationScenario(
                scenario_id=f"pair_scenario_{index}",
                category=category,
                user_prompt=f"Pair task {index}",
                evaluation_goal=f"Evaluate {category}",
                expected_success_behavior=["The combined result serves the user job"],
                evidence_to_collect=["A-only, B-only, and combined evidence"],
                expected_behavior=PairScenarioExpectedBehavior(
                    skill_a_only="A handles its declared responsibility.",
                    skill_b_only="B handles its declared responsibility.",
                    combined="The pair produces a coordinated result.",
                ),
            )
            for index, category in enumerate(categories, 1)
        ]


def _planning_kwargs():
    return {
        "scenario_generator": FakeScenarioGenerator(),
        "evidence_requirements_generator": ScenarioEvidenceRequirementsGenerator(),
    }


def _target() -> EvaluationTarget:
    return EvaluationTarget(
        target_id="skill-contract-1",
        project_id="lighttable",
        component_type="skill",
        name="recipe_planning",
        description="structured constrained meal planning",
        product_responsibility="help users obtain an executable meal plan",
        user_job="obtain a personalized meal plan",
        expected_behavior=["generate a structured plan while respecting constraints"],
        quality_dimensions=["constraint_adherence", "output_usability"],
        boundary=["must not bypass declared dietary constraints"],
        definition_status="declared",
    )


def _change() -> EvaluationChange:
    return EvaluationChange(
        change_id="change-1",
        project_id="lighttable",
        change_type="ablation",
        evaluation_type="skill_ablation",
        evaluation_name="Skill Ablation",
        summary="evaluate recipe planning capability",
    )


def test_generic_evolution_plan_preserves_skill_ablation_experiment_matrix() -> None:
    plan = build_evolution_plan(_target(), _change(), **_planning_kwargs())
    assert plan.status == "approved"
    assert plan.schema_version == "aig.evaluation-plan.v3"
    assert plan.component_type == "skill"
    assert plan.evaluation_type == "skill_ablation"
    assert [item.dimension for item in plan.dimensions] == ["trigger", "execution", "delivery", "boundary"]
    assert [item.experiment_kind for item in plan.experiments] == ["baseline", "removal", "equivalence"]
    assert [item.name for item in plan.experiments] == [
        "Full Capability Baseline",
        "Skill Removal",
        "Capability Equivalence",
    ]
    assert all(len(item.dimensions) == 4 for item in plan.experiments)
    assert all(item.control_group for item in plan.experiments)
    assert len(plan.scenarios) == 20
    assert len(plan.evidence_requirements) == 20
    assert plan.scenario_suite is not None
    assert plan.scenario_suite.scenarios_per_category == 5
    assert sum(item.repetition_count for item in plan.scenarios) == 28
    assert "enabled" not in json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    assert "replacement" not in json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)


def test_evaluation_knowledge_is_frozen_into_the_plan_context() -> None:
    knowledge = EvaluationKnowledge(
        project_id="lighttable",
        component_pattern="planning_skill",
        common_risks=["constraint violation"],
        recommended_dimensions=["boundary"],
        scenario_templates=["resource constraint"],
        source_evaluation_ids=["evaluation-previous"],
        evidence_refs=["sha256:previous-evidence"],
    )
    target = _target().model_copy(update={
        "component_pattern": "planning_skill",
        "evaluation_knowledge": [knowledge],
    })

    plan = build_evolution_plan(target, _change(), **_planning_kwargs())

    assert plan.evaluation_knowledge == [knowledge]
    assert plan.evaluation_knowledge[0].evidence_refs == ["sha256:previous-evidence"]


def test_generic_plan_can_add_optional_interaction_experiment() -> None:
    plan = build_evolution_plan(_target(), _change().model_copy(update={"related_target_ids": ["nutrition-knowledge"]}), **_planning_kwargs())
    assert plan.experiments[-1].experiment_kind == "interaction"
    assert plan.experiment_for_kind("interaction").name == "Skill Interaction"


def test_plan_rejects_project_mismatch() -> None:
    with pytest.raises(PlannerStrategyError, match="same project"):
        build_evolution_plan(_target(), _change().model_copy(update={"project_id": "other"}), **_planning_kwargs())


def test_evaluation_plan_is_not_mutable_after_generation() -> None:
    plan = build_evolution_plan(_target(), _change(), **_planning_kwargs())
    with pytest.raises(ValueError):
        plan.rationale = "changed"


def test_registry_selects_strategy_by_component_and_change_type() -> None:
    registry = PlannerStrategyRegistry()

    class ToolStrategy:
        component_type = "tool"
        change_type = "regression"

        def design(self, target, change, *, scenario_generator, evidence_requirements_generator) -> EvaluationPlanDesign:
                return EvalEngineeringDesignAssistant().design(
                    target,
                    change.model_copy(update={
                        "change_type": "ablation",
                        "scenario_suite": default_scenario_suite_config("skill"),
                    }),
                scenario_generator=scenario_generator,
                evidence_requirements_generator=evidence_requirements_generator,
            )

    registry.register(ToolStrategy())
    tool_target = _target().model_copy(update={"component_type": "tool", "name": "calendar_lookup"})
    tool_change = _change().model_copy(update={"change_type": "regression", "evaluation_type": "tool_regression"})
    plan = build_evolution_plan(tool_target, tool_change, registry=registry, **_planning_kwargs())
    assert plan.component_type == "tool"
    assert plan.evaluation_type == "tool_regression"


def test_default_registry_does_not_claim_unimplemented_strategies() -> None:
    tool_target = _target().model_copy(update={"component_type": "tool"})
    tool_change = _change().model_copy(update={"change_type": "regression", "evaluation_type": "tool_regression"})
    with pytest.raises(PlannerStrategyError, match="No Evaluation Planner strategy"):
        build_evolution_plan(tool_target, tool_change)


def test_default_registry_generates_explicit_skill_pair_matrix() -> None:
    pair_target = _target().model_copy(update={
        "target_id": "pair-planning-delivery",
        "component_type": "skill_pair",
        "name": "planning_and_delivery",
        "component_members": ["task_planning", "result_delivery"],
    })
    pair_change = _change().model_copy(update={
        "change_id": "pair-change",
        "change_type": "interaction",
        "evaluation_type": "skill_pair_evaluation",
    })

    plan = build_evolution_plan(
        pair_target,
        pair_change,
        scenario_generator=FakePairScenarioGenerator(),
        evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
    )

    assert plan.component_type == "skill_pair"
    assert plan.evaluation_type == "skill_pair_evaluation"
    assert [item.experiment_kind for item in plan.experiments] == [
        "pair_a_only", "pair_b_only", "pair_combined"
    ]
    assert [item.name for item in plan.experiments] == [
        "task_planning only", "result_delivery only", "task_planning + result_delivery"
    ]
    assert plan.component_members == ["task_planning", "result_delivery"]
    assert len(plan.scenarios) == 32
    assert {item.category for item in plan.scenarios} == {
        "complementary", "synergy", "conflict", "boundary"
    }
    assert plan.scenario_suite is not None
    assert plan.scenario_suite.scenarios_per_category == 8
    assert [item.dimension for item in plan.dimensions] == [
        "trigger", "capability_contribution", "synergy_gain", "coordination", "conflict", "reliability_cost"
    ]
    assert all(len(item.dimensions) == 6 for item in plan.evidence_requirements)


def test_skill_pair_planner_rejects_missing_members() -> None:
    pair_target = _target().model_copy(update={"component_type": "skill_pair", "component_members": ["only_one"]})
    pair_change = _change().model_copy(update={"change_type": "interaction", "evaluation_type": "skill_pair_evaluation"})

    with pytest.raises(PlannerStrategyError, match="exactly two"):
        build_evolution_plan(pair_target, pair_change, **_planning_kwargs())
