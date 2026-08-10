import pytest

from agentguard.domain import ProviderBinding
from agentguard.evidence_bundle import ImmutableEvidenceBundle
from agentguard.product_evaluation_analyst import ProductAnalystInput, ProductEvaluationAnalyst
from agentguard.provider_runtime import ProviderRuntimeError, ProviderToolCall, ProviderTurn
from agentguard.semantic_reporting import ProductDefinition


class FakeProvider:
    def __init__(self, arguments):
        self.arguments = arguments

    def complete(self, messages, tools):
        assert tools[0]["function"]["name"] == "submit_product_semantic_analysis"
        return ProviderTurn(
            "request-analyst-v4",
            "tool_calls",
            (ProviderToolCall("call-analyst-v4", "submit_product_semantic_analysis", self.arguments),),
            100,
            100,
            0,
            "request-fingerprint",
            "response-fingerprint",
        )


class RetryProvider(FakeProvider):
    def __init__(self, *arguments):
        self.payloads = list(arguments)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        self.arguments = self.payloads.pop(0)
        turn = super().complete(messages, tools)
        return ProviderTurn(
            f"request-analyst-retry-{self.calls}",
            turn.finish_reason,
            turn.tool_calls,
            turn.input_tokens,
            turn.output_tokens,
            turn.cache_hit_tokens,
            turn.request_fingerprint,
            turn.response_fingerprint,
        )


class RetrievalProvider(FakeProvider):
    def __init__(self, arguments):
        super().__init__(arguments)
        self.calls = 0
        self.initial_payload = None
        self.retrieved_payload = None

    def complete(self, messages, tools):
        self.calls += 1
        tool_names = [item["function"]["name"] for item in tools]
        assert tool_names[0] == "submit_product_semantic_analysis"
        if self.calls == 1:
            assert tool_names == ["submit_product_semantic_analysis", "read_evidence_refs"]
            self.initial_payload = __import__("json").loads(messages[1]["content"])
            return ProviderTurn(
                "request-retrieval-1",
                "tool_calls",
                (ProviderToolCall("call-retrieval-1", "read_evidence_refs", {"evidence_refs": ["ref-b"]}),),
                100, 20, 0, "request-1", "response-1",
            )
        assert tool_names == ["submit_product_semantic_analysis"]
        self.retrieved_payload = __import__("json").loads(messages[-1]["content"])
        turn = super().complete(messages, tools)
        return ProviderTurn(
            "request-retrieval-2",
            turn.finish_reason,
            turn.tool_calls,
            turn.input_tokens,
            turn.output_tokens,
            turn.cache_hit_tokens,
            turn.request_fingerprint,
            turn.response_fingerprint,
        )


def _binding() -> ProviderBinding:
    return ProviderBinding(
        project_id="demo",
        role="control_plane",
        provider="vllm",
        base_url="http://127.0.0.1:8000/v1",
        model="local",
        expected_environment_variable="FAKE_API_KEY",
        credential_source_ref="test",
        batch_budget_usd=0,
        timeout_seconds=10,
        allowed_hosts=["127.0.0.1"],
        data_retention_policy="test",
    )


def _input() -> ProductAnalystInput:
    evidence = ImmutableEvidenceBundle(
        evaluation_id="evaluation-demo",
        project_id="demo",
        evaluation_name="Tool Regression",
        evaluation_type="tool_regression",
        artifact_manifest_hash="sha256:1234567890abcdef",
        conditions=[
            {"condition_id": "condition-a", "label": "工具保持不变", "evidence_refs": ["ref-a"]},
            {"condition_id": "condition-b", "label": "工具实现发生变化", "evidence_refs": ["ref-b"]},
        ],
        facts=[
            {"fact_id": "fact-a", "label": "reference", "fact_type": "machine", "evidence_level": "verified", "evidence_refs": ["ref-a"]},
            {"fact_id": "fact-b", "label": "changed", "fact_type": "machine", "evidence_level": "verified", "evidence_refs": ["ref-b"]},
        ],
        records=[
            {"record_id": "record-a", "record_type": "trace", "source_ref": "source-a", "payload": {"trace": [{"event_type": "a"}]}, "evidence_refs": ["ref-a"]},
            {"record_id": "record-b", "record_type": "verifier_result", "source_ref": "source-b", "payload": {"oracle": {"outcome": "failed"}, "trace": [{"event_type": "b"}]}, "evidence_refs": ["ref-b"]},
        ],
        metrics=[
            {"metric_id": "metric-b", "name": "failure_count", "value": 1, "evidence_refs": ["ref-b"]},
        ],
        integrity={"status": "complete"},
    )
    return ProductAnalystInput(
        project_id="demo",
        evaluation_name="Tool Regression",
        evaluation_type="tool_regression",
        evaluation_question="工具变化是否改善用户任务完成？",
        hypothesis="替代工具实现可能改变结果可靠性。",
        product_definition=ProductDefinition(
            component_type="tool",
            component_name="calendar_lookup",
            description="读取日程",
            product_responsibility="帮助用户获得准确日程信息",
            user_job="查看可用时间",
            expected_behavior=["返回准确结果"],
            quality_dimensions=["accuracy"],
            boundary=["只读"],
            definition_status="declared",
            evidence_refs=["product-ref"],
        ),
        evidence=evidence,
    )


def _dimensions() -> list[dict[str, object]]:
    return [
        {"dimension": "trigger", "conclusion": "进入日程查询", "explanation": "任务产生了查询结果。", "status": "supported", "evidence_refs": ["ref-a"]},
        {"dimension": "execution", "conclusion": "流程基本完成", "explanation": "两种实现都返回结果。", "status": "partially_supported", "evidence_refs": ["ref-a", "ref-b"]},
        {"dimension": "delivery", "conclusion": "结果可用", "explanation": "用户可以查看结构化日程。", "status": "supported", "evidence_refs": ["ref-a", "ref-b"]},
        {"dimension": "boundary", "conclusion": "边界覆盖不足", "explanation": "尚未覆盖空结果。", "status": "unresolved", "evidence_refs": ["ref-a"]},
    ]


def _analysis() -> dict[str, object]:
    return {
        "product_overview": {
            "name": "calendar_lookup",
            "product_role": "读取日程并返回用户可用的时间信息",
            "why_it_exists": "用户需要可信的日程结果来安排后续行动",
            "user_problem": "减少用户手动查找和核对时间的成本",
            "ideal_behavior": ["正确理解查询范围", "返回准确结果"],
            "boundary": "只读，不修改用户日程",
            "evidence_refs": ["product-ref"],
        },
        "evaluation_context": {
            "items": [{"label": "用户任务", "value": "查看下周可用时间", "evidence_refs": ["ref-a"]}],
            "evidence_refs": ["ref-a"],
        },
        "executive_summary": {
            "final_conclusion": "当前证据支持工具变化没有破坏基本交付，但准确性仍需更多任务覆盖。",
            "status": "partially_supported",
            "dimensions": _dimensions(),
            "main_findings": [
                {"finding_type": "capability_value", "title": "能力价值", "statement": "用户可以获得结构化日程结果。", "evidence_refs": ["ref-a"]},
                {"finding_type": "replacement_risk", "title": "变化风险", "statement": "变化后的准确性仍需验证。", "evidence_refs": ["ref-b"]},
            ],
            "product_recommendation": "保留当前能力并补充边界任务。",
            "follow_up_priorities": ["增加空结果和跨日期任务"],
            "evidence_refs": ["ref-a", "ref-b"],
        },
        "experiment_overview": {
            "summary": "本次评估通过两类实验回答工具保持和变化后的产品问题。",
            "questions": [
                {"name": "完整能力验证", "question": "能力正常时用户是否获得可靠结果？", "purpose": "建立产品基线。", "evidence_refs": ["ref-a"]},
                {"name": "能力替换评估", "question": "未来替换实现后是否保持产品价值？", "purpose": "评估替换风险。", "evidence_refs": ["ref-b"]},
            ],
            "evidence_refs": ["ref-a", "ref-b"],
        },
        "experiment_analysis": [
            {"experiment_name": "完整能力验证", "purpose": "建立产品基线。", "design": "输入查看可用时间的任务并检查四个维度。", "input_scenario": "用户询问下周可用时间。", "observation": "返回结构化日程。", "result": "基础任务完成。", "product_meaning": "当前能力支撑日程查询。", "evidence_refs": ["ref-a"]},
            {"experiment_name": "能力替换评估", "purpose": "评估未来替换实现的风险。", "design": "使用同一任务比较当前和变化后的实现。", "input_scenario": "用户询问下周可用时间。", "observation": "变化后也返回结果。", "result": "基础交付保持。", "product_meaning": "替换实现仍需扩大验证。", "evidence_refs": ["ref-a", "ref-b"]},
        ],
        "scenario_stability": {
            "summary": "当前只有一个受控任务场景。",
            "coverage_conclusion": "证据不足以支持跨场景稳定性结论。",
            "status": "insufficient_evidence",
            "scenarios": [{"name": "场景 1", "user_prompt": "查看下周可用时间", "purpose": "测试普通查询。", "observation": "返回结构化结果。", "result": "任务完成。", "status": "supported", "evidence_refs": ["ref-a"]}],
            "evidence_refs": ["ref-a"],
        },
        "evidence_explorer": {
            "product_evidence": [{"label": "基本交付", "statement": "当前工具可以返回结构化日程结果。", "evidence_refs": ["ref-a"]}],
            "experiment_evidence": [{"experiment_name": "能力替换评估", "input_task": "查看下周可用时间", "reference_label": "当前实现", "reference_result": "返回结构化结果", "changed_label": "变化后的实现", "changed_result": "同样返回结果", "difference": "当前样本未显示基本交付差异", "evidence_refs": ["ref-a", "ref-b"]}],
        },
        "findings": [{"finding_id": "finding-accuracy", "finding_type": "product_effect", "observation": "两种实现都产生结果", "product_meaning": "当前证据支持基本可用性，但不足以证明更广任务上的准确性。", "impact_dimension": "accuracy", "direction": "unchanged", "severity": "medium", "interpretation_status": "partially_supported", "evidence_refs": ["ref-a", "ref-b"]}],
        "business_impact": {"affected_user_journey": "用户查看和安排日程", "user_consequence": "结果可用有助于用户安排时间，但准确性仍需验证。", "affected_capabilities": ["日程查询"], "severity": "medium", "release_relevance": "requires_review", "evidence_refs": ["ref-a", "ref-b"]},
        "recommendations": [{"recommendation_id": "recommendation-coverage", "priority": "medium", "target": "日程查询路径", "action": "增加跨日期、空结果和冲突时间的回归任务。", "reasoning": "当前证据只能支持基础任务结论。", "validation_plan": ["补充边界任务样本"], "evidence_refs": ["ref-a", "ref-b"]}],
        "limitations": [{"statement": "结论仅覆盖当前受控任务，稳定性证据不足。", "evidence_refs": ["ref-a"]}],
    }


def test_dynamic_analyst_accepts_context_summary_and_stability_boundary() -> None:
    result = ProductEvaluationAnalyst().analyze(_input(), provider=FakeProvider(_analysis()), binding=_binding())
    assert result.analysis.evaluation_context.items[0].label == "用户任务"
    assert result.analysis.executive_summary.dimensions[0].dimension == "trigger"
    assert result.analysis.experiment_overview.questions[1].name == "能力替换评估"
    assert result.analysis.scenario_stability.status == "insufficient_evidence"


def test_dynamic_analyst_rejects_unknown_evidence_reference() -> None:
    payload = _analysis()
    payload["business_impact"]["evidence_refs"] = ["not-in-bundle"]
    with pytest.raises(ProviderRuntimeError, match="outside the immutable bundle"):
        ProductEvaluationAnalyst().analyze(_input(), provider=FakeProvider(payload), binding=_binding())


def test_dynamic_analyst_requires_all_evaluation_dimensions() -> None:
    payload = _analysis()
    payload["executive_summary"]["dimensions"] = payload["executive_summary"]["dimensions"][:3]
    with pytest.raises(ProviderRuntimeError, match="invalid semantic analysis"):
        ProductEvaluationAnalyst().analyze(_input(), provider=FakeProvider(payload), binding=_binding())


def test_dynamic_analyst_rejects_citation_ids_as_product_copy() -> None:
    payload = _analysis()
    payload["product_overview"]["ideal_behavior"] = ["ref-a"]
    with pytest.raises(ProviderRuntimeError, match="narrative product field"):
        ProductEvaluationAnalyst().analyze(_input(), provider=FakeProvider(payload), binding=_binding())


def test_dynamic_analyst_rejects_stability_claim_with_one_scenario() -> None:
    payload = _analysis()
    payload["scenario_stability"]["status"] = "supported"
    with pytest.raises(ProviderRuntimeError, match="fewer than three scenarios"):
        ProductEvaluationAnalyst().analyze(_input(), provider=FakeProvider(payload), binding=_binding())


def test_dynamic_analyst_retries_once_after_contract_feedback() -> None:
    invalid = _analysis()
    invalid["executive_summary"]["follow_up_priorities"] = ["ref-a"]
    provider = RetryProvider(invalid, _analysis())
    result = ProductEvaluationAnalyst().analyze(_input(), provider=provider, binding=_binding())
    assert provider.calls == 2
    assert result.request_id == "request-analyst-retry-2"


def test_analyst_can_retrieve_complete_evidence_by_indexed_ref_before_submit() -> None:
    provider = RetrievalProvider(_analysis())
    analyst_input = _input()
    ref_a = analyst_input.evidence.conditions[0]
    conditions = [
        ref_a.model_copy(update={"condition_id": f"condition-a-{index}"})
        for index in range(5)
    ] + [analyst_input.evidence.conditions[1]]
    analyst_input = analyst_input.model_copy(update={
        "evidence": analyst_input.evidence.model_copy(update={"conditions": conditions}),
    })

    result = ProductEvaluationAnalyst().analyze(analyst_input, provider=provider, binding=_binding())

    assert provider.calls == 2
    assert provider.initial_payload["compact_evidence_index"]["available_evidence_refs"] == ["ref-a", "ref-b"]
    assert len(provider.initial_payload["compact_evidence_index"]["trials"]) == 6
    assert provider.initial_payload["evidence_access"]["initially_expanded_evidence_refs"] == ["ref-a"]
    assert provider.retrieved_payload["evidence_refs"] == ["ref-b"]
    assert provider.retrieved_payload["records"][0]["payload"]["oracle"]["outcome"] == "failed"
    assert provider.retrieved_payload["metrics"][0]["name"] == "failure_count"
    assert provider.retrieved_payload["retrieval_status"]["remaining_available_evidence_refs"] == []
    assert result.request_ids == ("request-retrieval-1", "request-retrieval-2")
    assert result.retrieved_evidence_refs == ("ref-b",)


def test_initial_expansion_is_limited_to_five_but_compact_index_is_complete() -> None:
    analyst_input = _input()
    template = analyst_input.evidence.conditions[0]
    conditions = [
        template.model_copy(update={
            "condition_id": f"condition-{index}",
            "scenario_id": f"scenario-{index}",
            "evidence_refs": ["ref-a"],
        })
        for index in range(7)
    ]
    dense_input = analyst_input.model_copy(update={
        "evidence": analyst_input.evidence.model_copy(update={"conditions": conditions}),
    })

    payload = ProductEvaluationAnalyst._provider_payload(dense_input)

    assert len(payload["compact_evidence_index"]["scenarios"]) == 7
    assert len(payload["compact_evidence_index"]["trials"]) == 7
    assert len(payload["initial_expanded_evidence_packs"]) == 5


def test_analyst_rejects_evidence_retrieval_outside_the_bundle() -> None:
    class UnknownRefProvider(FakeProvider):
        def complete(self, messages, tools):
            return ProviderTurn(
                "request-unknown-ref",
                "tool_calls",
                (ProviderToolCall("call-unknown-ref", "read_evidence_refs", {"evidence_refs": ["unknown"]}),),
                1, 1, 0, "request", "response",
            )

    with pytest.raises(ProviderRuntimeError, match="outside the immutable bundle"):
        ProductEvaluationAnalyst().analyze(_input(), provider=UnknownRefProvider(_analysis()), binding=_binding())
