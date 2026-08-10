import pytest

from agentguard.change_adapters import ToolRegressionEvaluationAdapter, build_v1_evaluation_adapter_layer
from agentguard.evaluation_adapters import AdapterContext


def _context(evaluation_type: str = "tool_regression") -> AdapterContext:
    return AdapterContext(
        project_id="demo",
        evaluation_name="Tool Regression",
        evaluation_type=evaluation_type,
        source_ref="artifact:tool-regression",
    )


def _artifact() -> dict[str, object]:
    return {
        "evaluation_id": "evaluation-tool",
        "evaluation_type": "tool_regression",
        "artifact_manifest_hash": "sha256:tool1234567890",
        "tool_name": "calendar_lookup",
        "baseline_metrics": {
            "tool_call_success": True,
            "argument_correct": True,
            "downstream_task_success": True,
            "latency_ms": 80,
            "cost_usd": 0.002,
        },
        "candidate_metrics": {
            "tool_call_success": True,
            "argument_correct": True,
            "downstream_task_success": True,
            "latency_ms": 85,
            "cost_usd": 0.002,
        },
        "baseline_trace": [{"event_type": "tool_call_completed"}],
        "candidate_trace": [{"event_type": "tool_call_completed"}],
        "baseline_output": {"events": ["A"]},
        "candidate_output": {"events": ["A", "B"]},
        "oracle": {"status": "verified", "evidence_refs": ["oracle:tool-state"]},
        "metrics": {"case_count": 2, "changed_case_count": 1},
        "evidence_refs": ["file:tool-baseline.json", "file:tool-candidate.json"],
    }


def test_tool_regression_adapter_normalizes_verified_v1_shape() -> None:
    bundle = ToolRegressionEvaluationAdapter().adapt(_artifact(), context=_context())

    assert bundle.evaluation_type == "tool_regression"
    assert bundle.project_id == "demo"
    assert [condition.label for condition in bundle.conditions] == ["保留 Tool 实现测试", "替换 Tool 实现测试"]
    assert bundle.type_data == {"tool_name": "calendar_lookup"}
    assert bundle.metrics[0].name == "case_count"
    assert bundle.artifact_manifest_hash == "sha256:tool1234567890"


def test_tool_regression_adapter_rejects_missing_evidence() -> None:
    artifact = _artifact()
    artifact.pop("evidence_refs")

    with pytest.raises(ValueError, match="evidence_refs"):
        ToolRegressionEvaluationAdapter().adapt(artifact, context=_context())


def test_tool_regression_adapter_rejects_wrong_context_type() -> None:
    with pytest.raises(ValueError, match="context type mismatch"):
        ToolRegressionEvaluationAdapter().adapt(_artifact(), context=_context("memory_evolution"))


def test_v1_adapter_registry_exposes_only_verified_component_types() -> None:
    layer = build_v1_evaluation_adapter_layer()

    assert layer.registered_types() == (
        "skill_ablation",
        "skill_pair_evaluation",
        "tool_regression",
    )
    for unsupported in ("memory_evolution", "prompt_change", "release_summary"):
        with pytest.raises(ValueError, match="No Evaluation Adapter"):
            layer.adapt(unsupported, {}, context=_context(unsupported))
