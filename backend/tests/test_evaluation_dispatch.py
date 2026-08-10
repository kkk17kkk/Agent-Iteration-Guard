from __future__ import annotations

import pytest

from agentguard.evaluation_dispatch import EvaluationDispatchError, build_evaluation_plan_for_request
from agentguard.evaluation_planning import EvaluationScenario, PairScenarioExpectedBehavior
from agentguard.evaluation_request import EvaluationRequest
from agentguard.evaluation_scenario_generator import ScenarioEvidenceRequirementsGenerator
from agentguard.evaluation_suite import scenario_category_sequence
from agentguard.interaction_evaluation import InteractionRelationshipProfile
from agentguard.project_intelligence import (
    AgentManifest,
    CapabilityRecord,
    ProjectIntelligenceRegistration,
    ProjectIntelligenceRepository,
    RuntimeProfile,
)
from agentguard.semantic_reporting import ProductDefinition
from agentguard.store import Store


class FakeEvaluationScenarioGenerator:
    def generate(self, target, change):
        categories = scenario_category_sequence(
            ("normal", "constraint_conflict", "boundary", "robustness"),
            change.scenario_suite,
        )
        return [
            EvaluationScenario(
                scenario_id=f"scenario_{index}",
                category=category,
                user_prompt=f"User task {index}",
                evaluation_goal=f"Evaluate {category}",
                expected_success_behavior=["The declared user task is completed."],
                evidence_to_collect=["structured output and trace"],
            )
            for index, category in enumerate(categories, start=1)
        ]

    def analyze_pair_relationship(self, target, change):
        return InteractionRelationshipProfile(
            relationship="complementary",
            rationale="The two capabilities serve distinct parts of one user job.",
            signals=["shared user job"],
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
                expected_success_behavior=["The combined result serves the user job."],
                evidence_to_collect=["A-only, B-only, and combined trace"],
                expected_behavior=PairScenarioExpectedBehavior(
                    skill_a_only="The first capability handles its responsibility.",
                    skill_b_only="The second capability handles its responsibility.",
                    combined="The pair produces a coordinated result.",
                ),
            )
            for index, category in enumerate(categories, start=1)
        ]


def _intelligence(tmp_path):
    project_id = "dispatch-agent"
    capabilities = [
        CapabilityRecord(
            project_id=project_id,
            component_type="skill",
            name="task_planning",
            responsibility="Turn a request into a plan.",
        ),
        CapabilityRecord(
            project_id=project_id,
            component_type="skill",
            name="result_delivery",
            responsibility="Deliver a usable result.",
        ),
        CapabilityRecord(
            project_id=project_id,
            component_type="skill_pair",
            name="planning_and_delivery",
            responsibility="Coordinate planning and delivery.",
            dependencies=["task_planning", "result_delivery"],
        ),
        CapabilityRecord(
            project_id=project_id,
            component_type="tool",
            name="lookup_catalog",
            responsibility="Read catalog data.",
        ),
    ]
    registration = ProjectIntelligenceRegistration(
        project_id=project_id,
        agent_manifest=AgentManifest(
            project_id=project_id,
            agent_name="Dispatch Agent",
            purpose="Complete bounded tasks.",
            source_kind="repository",
            source_ref="repo:dispatch-agent",
            available_components=[item.name for item in capabilities],
            capability_descriptions={item.name: item.responsibility for item in capabilities},
        ),
        capabilities=capabilities,
        runtime_profile=RuntimeProfile(
            project_id=project_id,
            entrypoint="python -m dispatch_agent",
            runtime_kind="native_command",
            execution_requirements=["isolated working directory"],
            source_ref="repo:dispatch-agent",
        ),
        baseline_version="baseline",
    )
    return ProjectIntelligenceRepository(Store(str(tmp_path / "dispatch.db"))).register(registration)


def _request(component_type: str, component_name: str, change_type: str) -> EvaluationRequest:
    return EvaluationRequest(
        project_id="dispatch-agent",
        component_type=component_type,
        component_name=component_name,
        change_type=change_type,
        candidate_version="candidate",
        baseline_version="baseline",
    )


def _definition(component_type: str, component_name: str) -> ProductDefinition:
    return ProductDefinition(
        component_type=component_type,
        component_name=component_name,
        description=f"{component_name} capability",
        product_responsibility="Complete the declared user job.",
        user_job="Obtain a usable result.",
    )


def _plan(request, intelligence, definition):
    return build_evaluation_plan_for_request(
        request,
        intelligence,
        definition,
        evaluation_name="Dispatch test",
        scenario_generator=FakeEvaluationScenarioGenerator(),
        evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
    )


def test_skill_request_dispatches_to_skill_ablation_strategy(tmp_path) -> None:
    intelligence = _intelligence(tmp_path)
    plan = _plan(
        _request("skill", "task_planning", "remove"),
        intelligence,
        _definition("skill", "task_planning"),
    )

    assert plan.component_type == "skill"
    assert plan.evaluation_type == "skill_ablation"
    assert [item.experiment_kind for item in plan.experiments] == ["baseline", "removal", "equivalence"]


def test_skill_pair_request_dispatches_to_pair_strategy(tmp_path) -> None:
    intelligence = _intelligence(tmp_path)
    plan = _plan(
        _request("skill_pair", "planning_and_delivery", "modify"),
        intelligence,
        _definition("skill_pair", "planning_and_delivery"),
    )

    assert plan.component_type == "skill_pair"
    assert plan.evaluation_type == "skill_pair_evaluation"
    assert [item.experiment_kind for item in plan.experiments] == ["pair_a_only", "pair_b_only", "pair_combined"]


def test_unregistered_skill_pair_request_dispatches_from_pair_members(tmp_path) -> None:
    intelligence = _intelligence(tmp_path)
    pair_name = "result_delivery__task_planning"
    request = _request("skill_pair", pair_name, "modify").model_copy(update={
        "pair_members": ["task_planning", "result_delivery"],
    })

    plan = _plan(request, intelligence, _definition("skill_pair", pair_name))

    assert plan.component_members == ["task_planning", "result_delivery"]
    assert plan.component_name == pair_name
    assert plan.evaluation_type == "skill_pair_evaluation"


def test_tool_request_is_explicitly_deferred_without_pair_fallback(tmp_path) -> None:
    intelligence = _intelligence(tmp_path)

    with pytest.raises(EvaluationDispatchError) as error:
        _plan(
            _request("tool", "lookup_catalog", "modify"),
            intelligence,
            _definition("tool", "lookup_catalog"),
        )

    assert error.value.code == "E_TOOL_PLANNER_DEFERRED"
    assert error.value.status == "deferred"
    assert "skill_pair" not in str(error.value)
