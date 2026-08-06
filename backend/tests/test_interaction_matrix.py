from pathlib import Path

import pytest

from agentguard.evaluation_planning import (
    EvaluationChange,
    EvaluationScenario,
    EvaluationTarget,
    PairScenarioExpectedBehavior,
    build_evolution_evaluation_plan,
    ScenarioProvenance,
    scenario_hash_for,
)
from agentguard.evaluation_scenario_generator import ScenarioEvidenceRequirementsGenerator
from agentguard.interaction_evaluation import (
    InteractionHypothesisSource,
    InteractionRelationshipProfile,
    PlanningCallMetadata,
)
from agentguard.interaction_matrix import (
    InteractionExecutionError,
    InteractionTrialResult,
    execute_interaction_matrix,
)
from agentguard.scenario_contracts import (
    FixtureCatalog,
    FixtureDescriptor,
    ScenarioInputContract,
    ScenarioInputRequirement,
    check_evaluation_plan_readiness,
)


class PairGenerator:
    def analyze_pair_relationship(self, target, change):
        return InteractionRelationshipProfile(
            relationship="complementary",
            rationale="The two capabilities serve one user job.",
            signals=["shared user job"],
            hypothesis_source=InteractionHypothesisSource(
                inputs=["description", "responsibility", "dependency", "boundary"]
            ),
            provider_metadata=PlanningCallMetadata(
                provider="test",
                model="relationship-model",
                request_fingerprint="request-fingerprint",
                response_fingerprint="response-fingerprint",
            ),
            hypothesis_hash="sha256:" + "a" * 64,
        )

    def generate_pair_scenarios(self, target, change, *, relationship):
        categories = ("complementary", "synergy", "conflict", "boundary")
        scenarios = [
            EvaluationScenario(
                scenario_id=f"scenario_{index}",
                category=category,
                user_prompt=f"Task {index}",
                evaluation_goal=f"Observe {category}",
                expected_success_behavior=["serve the user job"],
                evidence_to_collect=["trace and output"],
                expected_behavior=PairScenarioExpectedBehavior(
                    skill_a_only="A handles its responsibility.",
                    skill_b_only="B handles its responsibility.",
                    combined="The pair handles the user job.",
                ),
                input_contract=(
                    ScenarioInputContract(
                        profile_id="missing-input",
                        requirements=[ScenarioInputRequirement(
                            input_id="source-data",
                            fixture_id="source-data-absent",
                            availability="absent",
                            description="The boundary scenario has no source data.",
                        )],
                    )
                    if category == "boundary"
                    else ScenarioInputContract.no_input()
                ),
            )
            for index, category in enumerate(categories, 1)
        ]
        return [
            scenario.model_copy(update={
                "scenario_hash": scenario_hash_for(scenario.model_dump(mode="json")),
                "scenario_provenance": ScenarioProvenance(
                    hypothesis_source="eval_engineering.relationship_hypothesis",
                    relationship_hypothesis_hash=relationship.hypothesis_hash,
                    provider_metadata=relationship.provider_metadata,
                    scenario_hash=scenario_hash_for(scenario.model_dump(mode="json")),
                ),
            })
            for scenario in scenarios
        ]


def _plan():
    target = EvaluationTarget(
        target_id="pair-1",
        project_id="demo",
        component_type="skill_pair",
        name="a_and_b",
        description="Two capabilities",
        product_responsibility="Serve one user job",
        user_job="Complete the job",
        component_members=["a", "b"],
    )
    change = EvaluationChange(
        change_id="change-1",
        project_id="demo",
        change_type="interaction",
        evaluation_type="skill_pair_evaluation",
        evaluation_name="Pair",
        summary="Evaluate the pair",
    )
    return build_evolution_evaluation_plan(
        target,
        change,
        scenario_generator=PairGenerator(),
        evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
    )


class FakeRunner:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def run(self, scenario, condition_kind, *, trial_root: Path):
        self.calls.append((scenario.scenario_id, condition_kind))
        return InteractionTrialResult(
            scenario_id=scenario.scenario_id,
            condition_kind=condition_kind,
            category=scenario.category,
            label=f"{scenario.scenario_id}:{condition_kind}",
            observations={"task_success": True},
            trace=[{"event_type": "target_completed"}],
            output={"condition": condition_kind},
            metrics={"latency_ms": 10, "cost_usd": 0.001},
            oracle={
                "oracle_type": "rule_based",
                "oracle_version": "1.0",
                "validation_input": {"scenario_id": scenario.scenario_id, "condition": condition_kind},
                "status": "verified",
                "evidence_refs": [f"oracle:{scenario.scenario_id}:{condition_kind}"],
            },
            evidence_refs=[f"trial:{scenario.scenario_id}:{condition_kind}"],
        )


def _readiness(plan):
    return check_evaluation_plan_readiness(
        plan,
        FixtureCatalog(fixtures=[FixtureDescriptor(
            fixture_id="source-data-absent",
            kind="file",
            availability="absent",
            purpose="Boundary input is intentionally absent",
        )]),
    )


def test_executor_runs_exact_matrix_and_writes_artifact(tmp_path: Path) -> None:
    plan = _plan()
    readiness = _readiness(plan)
    runner = FakeRunner()
    output_path = tmp_path / "interaction-artifact.json"

    artifact = execute_interaction_matrix(
        plan,
        interaction_name="a_and_b",
        evaluation_id="evaluation-1",
        readiness=readiness,
        runner=runner,
        run_root=tmp_path / "runs",
        output_path=output_path,
    )

    assert len(runner.calls) == 12
    assert artifact.metrics["condition_count"] == 12
    assert artifact.metrics["expected_condition_count"] == 12
    assert artifact.integrity["status"] == "complete"
    assert output_path.is_file()


def test_executor_refuses_blocked_readiness_before_running_target(tmp_path: Path) -> None:
    plan = _plan()
    blocked = _readiness(plan).model_copy(update={
        "status": "blocked",
        "blocking_reasons": ["boundary fixture is not configured"],
    })
    runner = FakeRunner()

    with pytest.raises(InteractionExecutionError, match="Scenario Readiness is blocked"):
        execute_interaction_matrix(
            plan,
            interaction_name="a_and_b",
            evaluation_id="evaluation-1",
            readiness=blocked,
            runner=runner,
            run_root=tmp_path / "runs",
        )

    assert runner.calls == []
