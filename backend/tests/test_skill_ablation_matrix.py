from __future__ import annotations

from pathlib import Path

import pytest

from agentguard.evaluation_planning import (
    EvaluationChange,
    EvaluationScenario,
    EvaluationTarget,
    ScenarioProvenance,
    scenario_hash_for,
    build_evolution_evaluation_plan,
)
from agentguard.evaluation_adapters import AdapterContext
from agentguard.evaluation_scenario_generator import ScenarioEvidenceRequirementsGenerator
from agentguard.interaction_evaluation import PlanningCallMetadata
from agentguard.interaction_matrix import InteractionTrialResult
from agentguard.scenario_contracts import (
    FixtureCatalog,
    FixtureDescriptor,
    ScenarioInputContract,
    ScenarioInputRequirement,
    check_evaluation_plan_readiness,
)
from agentguard.skill_ablation import (
    SkillAblationExecutionError,
    execute_skill_ablation_matrix,
)
from agentguard.skill_ablation_adapter import skill_ablation_experiment_ids_by_condition
from agentguard.change_adapters import build_v1_evaluation_adapter_layer


class SkillScenarioGenerator:
    def generate(self, target, change):
        metadata = PlanningCallMetadata(
            provider="test",
            model="scenario-model",
            request_fingerprint="request-fingerprint",
            response_fingerprint="response-fingerprint",
        )
        scenarios = []
        for index, category in enumerate(("normal", "constraint_conflict", "boundary"), start=1):
            scenario = EvaluationScenario(
                scenario_id=f"skill_scenario_{index}",
                category=category,
                user_prompt=f"Skill task {index}",
                evaluation_goal=f"Evaluate {category}",
                expected_success_behavior=["The declared Skill deliverable is produced."],
                evidence_to_collect=["target trace, usage, and independent oracle"],
                input_contract=(
                    ScenarioInputContract(
                        profile_id="skill-boundary-input",
                        requirements=[ScenarioInputRequirement(
                            input_id="missing-source",
                            fixture_id="missing-source",
                            availability="absent",
                            description="The boundary case has no source input.",
                        )],
                    )
                    if category == "boundary"
                    else ScenarioInputContract.no_input()
                ),
            )
            frozen_hash = scenario_hash_for(scenario.model_dump(mode="json"))
            scenarios.append(scenario.model_copy(update={
                "scenario_hash": frozen_hash,
                "scenario_provenance": ScenarioProvenance(
                    hypothesis_source="eval_engineering.skill_ablation",
                    provider_metadata=metadata,
                    scenario_hash=frozen_hash,
                ),
            }))
        return scenarios


class SkillRunner:
    def __init__(self, *, live_evidence: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.live_evidence = live_evidence

    def run(self, scenario, condition_kind, *, trial_root: Path):
        self.calls.append((scenario.scenario_id, condition_kind))
        return InteractionTrialResult(
            scenario_id=scenario.scenario_id,
            condition_kind=condition_kind,
            category=scenario.category,
            label=f"{scenario.category} / {condition_kind}",
            observations={"deliverable_present": True},
            trace=[{"event_type": "skill_completed"}],
            output={"condition": condition_kind},
            metrics={"latency_ms": 10, "cost_usd": 0.001},
            oracle={
                "verifier_id": "skill-oracle",
                "oracle_type": "rule_based",
                "oracle_version": "1.0",
                "validation_input": {"scenario_id": scenario.scenario_id},
                "status": "verified",
                "outcome": "passed",
                "assertions": [{"name": "deliverable", "status": "passed", "detail": "valid"}],
                "evidence_refs": [f"oracle:{scenario.scenario_id}:{condition_kind}"],
                "summary": "Independent verifier passed.",
            },
            evidence_refs=[f"trial:{scenario.scenario_id}:{condition_kind}"],
            provider_request_ids=["provider-request-1"] if self.live_evidence else [],
            usage={"input_tokens": 10, "output_tokens": 5} if self.live_evidence else {},
            output_artifact_ref="artifact:output" if self.live_evidence else None,
        )


def _plan():
    target = EvaluationTarget(
        target_id="skill-1",
        project_id="demo",
        component_type="skill",
        name="profile_skill",
        description="Build a structured profile.",
        product_responsibility="Provide a usable profile.",
        user_job="Understand the application.",
    )
    change = EvaluationChange(
        change_id="change-1",
        project_id="demo",
        change_type="ablation",
        evaluation_type="skill_ablation",
        evaluation_name="Profile Skill",
        summary="Evaluate profile Skill contribution.",
    )
    return build_evolution_evaluation_plan(
        target,
        change,
        scenario_generator=SkillScenarioGenerator(),
        evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
    )


def _readiness(plan):
    return check_evaluation_plan_readiness(
        plan,
        FixtureCatalog(fixtures=[FixtureDescriptor(
            fixture_id="missing-source",
            kind="file",
            availability="absent",
            purpose="Boundary input is intentionally absent.",
        )]),
    )


def test_skill_matrix_runs_three_conditions_per_frozen_scenario(tmp_path: Path) -> None:
    plan = _plan()
    readiness = _readiness(plan)
    runner = SkillRunner()

    artifact = execute_skill_ablation_matrix(
        plan,
        evaluation_id="evaluation-skill-1",
        readiness=readiness,
        runner=runner,
        run_root=tmp_path / "runs",
        output_path=tmp_path / "matrix.json",
    )

    assert readiness.status == "ready"
    assert len(runner.calls) == 9
    assert artifact.condition_kinds == ["baseline", "removal", "replacement"]
    assert artifact.metrics["condition_count"] == 9
    assert artifact.metrics["verified_condition_count"] == 9
    assert (tmp_path / "matrix.json").is_file()


def test_skill_matrix_rejects_missing_live_provider_and_artifact_evidence(tmp_path: Path) -> None:
    plan = _plan()
    readiness = _readiness(plan)

    with pytest.raises(ValueError, match="required live evidence"):
        execute_skill_ablation_matrix(
            plan,
            evaluation_id="evaluation-skill-1",
            readiness=readiness,
            runner=SkillRunner(live_evidence=False),
            run_root=tmp_path / "runs",
        )


def test_skill_matrix_adapts_to_immutable_evidence_with_oracle_facts(tmp_path: Path) -> None:
    plan = _plan()
    readiness = _readiness(plan)
    artifact = execute_skill_ablation_matrix(
        plan,
        evaluation_id="evaluation-skill-1",
        readiness=readiness,
        runner=SkillRunner(),
        run_root=tmp_path / "runs",
    )
    bundle = build_v1_evaluation_adapter_layer().adapt(
        "skill_ablation",
        artifact,
        context=AdapterContext(
            project_id="demo",
            evaluation_name=plan.evaluation_name,
            evaluation_type="skill_ablation",
            component_name=plan.component_name,
            source_ref="matrix:test",
            evaluation_plan_id=plan.plan_id,
            experiment_ids_by_condition=skill_ablation_experiment_ids_by_condition(plan),
        ),
    )

    assert bundle.evaluation_type == "skill_ablation"
    assert len(bundle.conditions) == 9
    assert all(item.observations["oracle_verified"] is True for item in bundle.conditions)
    assert bundle.type_data["condition_kinds"] == ["baseline", "removal", "replacement"]
