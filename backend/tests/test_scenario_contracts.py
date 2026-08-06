import hashlib
from pathlib import Path

from agentguard.evaluation_planning import EvaluationPlan, EvaluationScenario
from agentguard.scenario_contracts import (
    FixtureCatalog,
    FixtureDescriptor,
    ScenarioInputContract,
    ScenarioInputRequirement,
    ScenarioTraceContract,
    check_evaluation_plan_readiness,
    check_scenario_readiness,
    verify_scenario_trace_contract,
)


def _requirement(*, fixture_id: str, availability: str = "present") -> ScenarioInputRequirement:
    return ScenarioInputRequirement(
        input_id="task-data",
        fixture_id=fixture_id,
        availability=availability,
        description="Input state required by the scenario.",
    )


def test_present_fixture_requires_matching_materialized_file(tmp_path: Path) -> None:
    payload = b"uid,value\nuser-1,7\n"
    (tmp_path / "input.csv").write_bytes(payload)
    catalog = FixtureCatalog(fixtures=[FixtureDescriptor(
        fixture_id="input-present",
        kind="file",
        availability="present",
        source_ref="input.csv",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        purpose="Stable evaluation input",
    )])
    result = check_scenario_readiness(
        scenario_id="scenario-1",
        category="normal",
        input_contract=ScenarioInputContract(profile_id="input_present", requirements=[_requirement(fixture_id="input-present")]),
        fixture_catalog=catalog,
        fixture_root=tmp_path,
    )

    assert result.status == "ready"
    assert result.blocking_reasons == []


def test_absent_fixture_is_a_first_class_state_and_existing_input_blocks(tmp_path: Path) -> None:
    catalog = FixtureCatalog(fixtures=[FixtureDescriptor(
        fixture_id="input-absent",
        kind="file",
        availability="absent",
        source_ref="missing.csv",
        purpose="The boundary case intentionally has no input file",
    )])
    contract = ScenarioInputContract(
        profile_id="input_missing",
        requirements=[_requirement(fixture_id="input-absent", availability="absent")],
    )
    assert check_scenario_readiness(
        scenario_id="boundary-1",
        category="boundary",
        input_contract=contract,
        fixture_catalog=catalog,
        fixture_root=tmp_path,
    ).status == "ready"

    (tmp_path / "missing.csv").write_text("unexpected", encoding="utf-8")
    result = check_scenario_readiness(
        scenario_id="boundary-1",
        category="boundary",
        input_contract=contract,
        fixture_catalog=catalog,
        fixture_root=tmp_path,
    )
    assert result.status == "blocked"
    assert any("declared absent" in item for item in result.blocking_reasons)


def test_boundary_without_input_contract_is_blocked() -> None:
    result = check_scenario_readiness(
        scenario_id="boundary-1",
        category="boundary",
        input_contract=ScenarioInputContract.no_input(),
        fixture_catalog=FixtureCatalog(),
    )

    assert result.status == "blocked"
    assert any("must declare" in item for item in result.blocking_reasons)


def test_scenario_trace_contract_can_forbid_provider_usage_on_missing_input() -> None:
    contract = ScenarioInputContract(
        profile_id="missing-input",
        requirements=[_requirement(fixture_id="missing", availability="absent")],
        trace=ScenarioTraceContract(
            provider_usage="forbidden",
            required_event_types=["clarification_requested"],
        ),
    )

    assert verify_scenario_trace_contract(contract, [{"event_type": "clarification_requested"}]) == []
    assert verify_scenario_trace_contract(contract, [{"event_type": "provider_completed", "request_id": "req-1"}])


def test_condition_trace_contracts_allow_matrix_arms_to_have_different_events() -> None:
    contract = ScenarioInputContract(
        profile_id="pair-input",
        trace=ScenarioTraceContract(provider_usage="optional"),
        condition_traces={
            "a_only": ScenarioTraceContract(required_event_types=["skill_a_completed"]),
            "b_only": ScenarioTraceContract(required_event_types=["skill_b_completed"]),
            "combined": ScenarioTraceContract(required_event_types=["skill_a_completed", "skill_b_completed"]),
        },
    )

    assert verify_scenario_trace_contract(
        contract,
        [{"event_type": "skill_a_completed"}],
        condition_kind="a_only",
    ) == []
    assert verify_scenario_trace_contract(
        contract,
        [{"event_type": "skill_a_completed"}],
        condition_kind="b_only",
    )


def test_plan_readiness_covers_every_scenario() -> None:
    plan = EvaluationPlan(
        plan_id="plan-1",
        project_id="demo",
        target_id="pair-1",
        change_id="change-1",
        change_type="interaction",
        evaluation_type="skill_pair_evaluation",
        evaluation_name="Pair",
        component_type="skill_pair",
        component_name="a_and_b",
        product_responsibility="Combine capabilities",
        user_job="Complete a task",
        rationale="Test interaction",
        hypothesis="Combined capability is useful",
        dimensions=[
            {"dimension": "trigger", "question": "When?", "success_criteria": ["trigger"], "evidence_to_collect": ["trace"]},
            {"dimension": "capability_contribution", "question": "Value?", "success_criteria": ["value"], "evidence_to_collect": ["output"]},
            {"dimension": "synergy_gain", "question": "Synergy?", "success_criteria": ["synergy"], "evidence_to_collect": ["output"]},
            {"dimension": "coordination", "question": "Handoff?", "success_criteria": ["handoff"], "evidence_to_collect": ["trace"]},
        ],
        experiments=[{
            "experiment_id": "exp-1", "experiment_kind": "pair_combined", "name": "Combined", "purpose": "Test", "design": "A+B",
            "control_group": "A", "comparison": "A+B", "dimensions": ["trigger"], "success_criteria": ["success"],
        }],
        comparison_question="Is the pair useful?",
        scenarios=[
            EvaluationScenario(
                scenario_id="normal-1", category="normal", user_prompt="Do task", evaluation_goal="Observe", expected_success_behavior=["done"], evidence_to_collect=["output"]
            ),
            EvaluationScenario(
                scenario_id="boundary-1", category="boundary", user_prompt="Missing input", evaluation_goal="Observe boundary", expected_success_behavior=["clarify"], evidence_to_collect=["output"],
                input_contract=ScenarioInputContract(profile_id="missing", requirements=[_requirement(fixture_id="missing", availability="absent")]),
            ),
            EvaluationScenario(
                scenario_id="conflict-1", category="conflict", user_prompt="Resolve conflict", evaluation_goal="Observe", expected_success_behavior=["resolve"], evidence_to_collect=["output"]
            ),
        ],
        evidence_requirements=[
            {"requirement_id": "req-1", "scenario_id": "normal-1", "dimensions": ["trigger"], "evidence_to_collect": ["output"]},
            {"requirement_id": "req-2", "scenario_id": "boundary-1", "dimensions": ["boundary"], "evidence_to_collect": ["output"]},
            {"requirement_id": "req-3", "scenario_id": "conflict-1", "dimensions": ["conflict"], "evidence_to_collect": ["output"]},
        ],
        overall_success_criteria=["verified"],
    )
    result = check_evaluation_plan_readiness(
        plan,
        FixtureCatalog(fixtures=[FixtureDescriptor(
            fixture_id="missing", kind="file", availability="absent", purpose="Missing input",
        )]),
    )

    assert result.status == "ready"
    assert {item.scenario_id for item in result.scenarios} == {"normal-1", "boundary-1", "conflict-1"}
