import pytest

from agentguard.domain import ProviderBinding
from agentguard.evaluation_planning import EvaluationChange, EvaluationScenario, EvaluationTarget
from agentguard.evaluation_scenario_generator import (
    LLMEvaluationScenarioGenerator,
    ScenarioEvidenceRequirementsGenerator,
)
from agentguard.provider_runtime import ProviderRuntimeError, ProviderToolCall, ProviderTurn


class FakeProvider:
    def __init__(self, arguments):
        self.arguments = arguments

    def complete(self, messages, tools):
        assert tools[0]["function"]["name"] == "submit_evaluation_scenarios"
        assert isinstance(messages[1]["content"], str)
        return ProviderTurn(
            "scenario-request",
            "tool_calls",
            (ProviderToolCall("scenario-call", "submit_evaluation_scenarios", self.arguments),),
            100,
            100,
            0,
            "request-fingerprint",
            "response-fingerprint",
        )


class FailOnceProvider(FakeProvider):
    def __init__(self, arguments):
        super().__init__(arguments)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            raise ProviderRuntimeError("Provider returned an invalid Chat Completions payload (malformed tool JSON).")
        return super().complete(messages, tools)


class PairFakeProvider:
    def __init__(self, relationship: str, categories: tuple[str, ...]):
        self.relationship = relationship
        self.categories = categories
        self.calls: list[str] = []

    def complete(self, messages, tools):
        name = tools[0]["function"]["name"]
        self.calls.append(name)
        assert "scenario" in messages[0]["content"].lower()
        if name == "submit_pair_relationship":
            arguments = {
                "relationship": self.relationship,
                "rationale": "The declared responsibilities suggest this interaction shape.",
                "signals": ["shared user job", "declared handoff"],
            }
        else:
            arguments = {
                "scenarios": [
                    {
                        "scenario_id": f"llm-pair-{index}",
                        "category": category,
                        "user_prompt": f"A realistic pair task {index}",
                        "evaluation_goal": f"Observe {category} behavior",
                        "expected_behavior": {
                            "skill_a_only": "A handles its own responsibility.",
                            "skill_b_only": "B handles its own responsibility.",
                            "combined": "The combined result serves the user job.",
                        },
                        "evidence_to_collect": ["activation, handoff, output, latency, and cost"],
                    }
                    for index, category in enumerate(self.categories, 1)
                ]
            }
        return ProviderTurn(
            "pair-request",
            "tool_calls",
            (ProviderToolCall("pair-call", name, arguments),),
            100,
            100,
            0,
            "request-fingerprint",
            "response-fingerprint",
        )


def _binding() -> ProviderBinding:
    return ProviderBinding(
        project_id="demo",
        role="control_plane",
        provider="deepseek",
        base_url="http://127.0.0.1:8000/v1",
        model="deepseek",
        expected_environment_variable="API_KEY",
        credential_source_ref="test",
        batch_budget_usd=0,
        timeout_seconds=10,
        allowed_hosts=["127.0.0.1"],
        data_retention_policy="test",
    )


def _target() -> EvaluationTarget:
    return EvaluationTarget(
        target_id="target-1",
        project_id="demo",
        component_type="skill",
        name="recipe_planning",
        description="规划满足饮食约束的餐食方案",
        product_responsibility="生成可执行餐食方案",
        user_job="安排家庭餐食",
        expected_behavior=["遵守忌口和时间约束"],
        boundary=["不能忽略用户明确忌口"],
    )


def _change() -> EvaluationChange:
    return EvaluationChange(
        change_id="change-1",
        project_id="demo",
        change_type="ablation",
        evaluation_type="skill_ablation",
        evaluation_name="Skill Ablation",
        summary="验证餐食规划能力的产品价值",
    )


def _pair_target() -> EvaluationTarget:
    return _target().model_copy(update={
        "component_type": "skill_pair",
        "name": "recipe_planning_and_nutrition_check",
        "component_members": ["recipe_planning", "nutrition_check"],
    })


def _scenarios() -> list[dict[str, object]]:
    return [
        {"scenario_id": "llm-id-1", "category": "normal", "user_prompt": "安排两人晚餐。", "evaluation_goal": "测试普通任务。", "expected_success_behavior": ["输出方案"], "evidence_to_collect": ["最终交付"]},
        {"scenario_id": "llm-id-2", "category": "constraint_conflict", "user_prompt": "减脂但优先消耗库存鸡蛋。", "evaluation_goal": "测试冲突约束。", "expected_success_behavior": ["保持忌口"], "evidence_to_collect": ["约束结果"]},
        {"scenario_id": "llm-id-3", "category": "boundary", "user_prompt": "只能做一道菜。", "evaluation_goal": "测试边界。", "expected_success_behavior": ["减少范围"], "evidence_to_collect": ["边界行为"]},
    ]


def test_llm_scenario_generator_normalizes_ids_and_covers_required_categories() -> None:
    result = LLMEvaluationScenarioGenerator(FakeProvider({"scenarios": _scenarios()}), _binding()).generate(_target(), _change())
    assert [item.scenario_id for item in result] == ["scenario_1", "scenario_2", "scenario_3"]
    assert [item.category for item in result] == ["normal", "constraint_conflict", "boundary"]


def test_llm_scenario_generator_rejects_missing_required_category() -> None:
    scenarios = _scenarios()
    scenarios[-1]["category"] = "normal"
    with pytest.raises(ProviderRuntimeError, match="normal, constraint_conflict, and boundary"):
        LLMEvaluationScenarioGenerator(FakeProvider({"scenarios": scenarios}), _binding()).generate(_target(), _change())


def test_llm_scenario_generator_retries_one_malformed_provider_payload() -> None:
    provider = FailOnceProvider({"scenarios": _scenarios()})
    result = LLMEvaluationScenarioGenerator(provider, _binding()).generate(_target(), _change())
    assert provider.calls == 2
    assert len(result) == 3


def test_scenario_evidence_requirements_cover_each_scenario() -> None:
    scenarios = [EvaluationScenario.model_validate(item) for item in _scenarios()]
    requirements = ScenarioEvidenceRequirementsGenerator().generate(_target(), _change(), scenarios)
    assert [item.scenario_id for item in requirements] == ["llm-id-1", "llm-id-2", "llm-id-3"]
    assert all(item.dimensions == ["trigger", "execution", "delivery", "boundary"] for item in requirements)


def test_pair_scenario_generator_uses_pair_prompt_and_complementary_matrix() -> None:
    provider = PairFakeProvider(
        "complementary",
        ("complementary", "synergy", "conflict", "boundary"),
    )
    generator = LLMEvaluationScenarioGenerator(provider, _binding())

    relationship = generator.analyze_pair_relationship(_pair_target(), _change())
    scenarios = generator.generate_pair_scenarios(_pair_target(), _change(), relationship=relationship)

    assert provider.calls == ["submit_pair_relationship", "submit_pair_evaluation_scenarios"]
    assert [item.category for item in scenarios] == [
        "complementary", "synergy", "conflict", "boundary"
    ]
    assert scenarios[0].expected_behavior is not None
    assert scenarios[0].expected_behavior.combined == "The combined result serves the user job."


def test_pair_scenario_generator_selects_competitive_policy_without_fixed_five_categories() -> None:
    provider = PairFakeProvider("competitive", ("conflict", "single_skill_dominant", "boundary"))
    generator = LLMEvaluationScenarioGenerator(provider, _binding())

    relationship = generator.analyze_pair_relationship(_pair_target(), _change())
    scenarios = generator.generate_pair_scenarios(_pair_target(), _change(), relationship=relationship)

    assert len(scenarios) == 3
    assert [item.category for item in scenarios] == ["conflict", "single_skill_dominant", "boundary"]


def test_pair_scenario_generator_rejects_category_counts_that_do_not_match_relationship() -> None:
    provider = PairFakeProvider("competitive", ("conflict", "conflict", "boundary"))
    generator = LLMEvaluationScenarioGenerator(provider, _binding())

    relationship = generator.analyze_pair_relationship(_pair_target(), _change())
    with pytest.raises(ProviderRuntimeError, match="wrong category matrix"):
        generator.generate_pair_scenarios(_pair_target(), _change(), relationship=relationship)
