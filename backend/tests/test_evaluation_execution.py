import pytest

from agentguard.evaluation_execution import (
    EvaluationExecutionError,
    build_evaluation_execution_mapping,
    parse_execution_scenario_mapping,
)
from agentguard.evaluation_planning import (
    EvaluationDimensionPlan,
    EvaluationEvidenceRequirement,
    EvaluationExperiment,
    EvaluationPlan,
    EvaluationScenario,
)


def _plan() -> EvaluationPlan:
    scenarios = [
        EvaluationScenario(
            scenario_id="scenario_1",
            category="normal",
            user_prompt="安排一顿家庭晚餐。",
            evaluation_goal="测试主要任务。",
            expected_success_behavior=["输出可执行方案"],
            evidence_to_collect=["最终交付"],
        ),
        EvaluationScenario(
            scenario_id="scenario_2",
            category="constraint_conflict",
            user_prompt="在冲突限制下安排晚餐。",
            evaluation_goal="测试冲突约束。",
            expected_success_behavior=["保持关键限制"],
            evidence_to_collect=["约束结果"],
        ),
        EvaluationScenario(
            scenario_id="scenario_3",
            category="boundary",
            user_prompt="只能做一道菜时怎么办？",
            evaluation_goal="测试边界。",
            expected_success_behavior=["合理缩小范围"],
            evidence_to_collect=["边界行为"],
        ),
    ]
    return EvaluationPlan(
        plan_id="plan-execution-test",
        project_id="demo",
        target_id="target-1",
        change_id="change-1",
        change_type="ablation",
        evaluation_type="skill_ablation",
        evaluation_name="Skill Ablation",
        component_type="skill",
        component_name="recipe_planning",
        product_responsibility="生成可执行餐食方案",
        user_job="安排家庭餐食",
        rationale="判断能力是否改善产品结果。",
        hypothesis="移除能力会损失约束处理。",
        dimensions=[
            EvaluationDimensionPlan(
                dimension=dimension,
                question=f"检查 {dimension}。",
                success_criteria=["有可复核证据"],
                evidence_to_collect=["观察"],
            )
            for dimension in ("trigger", "execution", "delivery", "boundary")
        ],
        experiments=[
            EvaluationExperiment(
                experiment_id="experiment-baseline",
                experiment_kind="baseline",
                name="Full Capability Baseline",
                purpose="建立基线。",
                design="运行完整能力。",
                control_group="完整能力",
                comparison="用户得到什么结果？",
                dimensions=["trigger", "execution", "delivery", "boundary"],
                success_criteria=["交付可用"],
            )
        ],
        comparison_question="能力是否改善用户结果？",
        scenarios=scenarios,
        evidence_requirements=[
            EvaluationEvidenceRequirement(
                requirement_id=f"requirement-{scenario.scenario_id}",
                scenario_id=scenario.scenario_id,
                dimensions=["trigger", "execution", "delivery", "boundary"],
                evidence_to_collect=scenario.evidence_to_collect,
            )
            for scenario in scenarios
        ],
        overall_success_criteria=["证据完整"],
    )


def test_execution_mapping_is_plan_scoped_and_complete() -> None:
    result = build_evaluation_execution_mapping(
        _plan(),
        ["enabled-1", "disabled-1"],
        {"enabled-1": "scenario_1", "disabled-1": "scenario_1"},
    )
    assert result.evaluation_plan_id == "plan-execution-test"
    assert result.by_trial_ref() == {"enabled-1": "scenario_1", "disabled-1": "scenario_1"}


def test_execution_mapping_rejects_missing_trials_and_unknown_scenarios() -> None:
    with pytest.raises(EvaluationExecutionError, match="missing"):
        build_evaluation_execution_mapping(_plan(), ["enabled-1", "disabled-1"], {"enabled-1": "scenario_1"})
    with pytest.raises(EvaluationExecutionError, match="outside"):
        build_evaluation_execution_mapping(_plan(), ["enabled-1"], {"enabled-1": "scenario_99"})


def test_execution_mapping_parser_accepts_nested_cli_shape() -> None:
    assert parse_execution_scenario_mapping(
        {"scenario_ids_by_trial_ref": {"enabled-1": "scenario_1"}}
    ) == {"enabled-1": "scenario_1"}
