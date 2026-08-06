import pytest

from agentguard.change_adapters import ToolRegressionEvaluationAdapter
from agentguard.evaluation_adapters import AdapterContext
from agentguard.tool_regression import ToolRegressionValidationError, validate_tool_regression_artifact


def _artifact() -> dict[str, object]:
    return {
        "evaluation_id": "evaluation-tool-v1",
        "evaluation_type": "tool_regression",
        "artifact_manifest_hash": "sha256:tool-v1-1234567890",
        "tool_name": "catalog_lookup",
        "baseline_output": {"items": ["A"]},
        "candidate_output": {"items": ["A", "B"]},
        "baseline_trace": [{"event_type": "tool_call_completed"}],
        "candidate_trace": [{"event_type": "tool_call_completed"}],
        "baseline_metrics": {
            "tool_call_success": True,
            "argument_correct": True,
            "downstream_task_success": True,
            "latency_ms": 80,
            "cost_usd": 0.002,
        },
        "candidate_metrics": {
            "tool_call_success": True,
            "argument_correct": False,
            "downstream_task_success": False,
            "latency_ms": 110,
            "cost_usd": 0.003,
        },
        "oracle": {"status": "verified", "evidence_refs": ["oracle:catalog-state"]},
        "metrics": {"case_count": 1, "latency_delta_ms": 30, "cost_delta_usd": 0.001},
        "evidence_refs": ["trace:baseline", "trace:candidate", "oracle:catalog-state"],
    }


def _context() -> AdapterContext:
    return AdapterContext(
        project_id="demo",
        evaluation_name="Tool Regression",
        evaluation_type="tool_regression",
        source_ref="artifact:tool-v1",
    )


def test_tool_regression_requires_independent_oracle_and_explicit_metrics() -> None:
    validate_tool_regression_artifact(_artifact(), expected_tool_name="catalog_lookup")

    invalid = _artifact()
    invalid["oracle"] = {"status": "inferred", "evidence_refs": ["oracle:guess"]}
    with pytest.raises(ToolRegressionValidationError, match="E_TOOL_ORACLE_UNVERIFIED"):
        validate_tool_regression_artifact(invalid)

    invalid = _artifact()
    del invalid["candidate_metrics"]
    with pytest.raises(ToolRegressionValidationError, match="E_TOOL_METRICS_MISSING"):
        validate_tool_regression_artifact(invalid)

    invalid = _artifact()
    invalid.pop("candidate_trace")
    with pytest.raises(ToolRegressionValidationError, match="E_TOOL_TRACE_MISSING"):
        validate_tool_regression_artifact(invalid)


def test_tool_regression_bundle_keeps_call_argument_downstream_latency_and_cost_facts() -> None:
    bundle = ToolRegressionEvaluationAdapter().adapt(_artifact(), context=_context())

    assert bundle.conditions[0].observations["tool_call_success"] is True
    assert bundle.conditions[1].observations["argument_correct"] is False
    assert bundle.conditions[1].observations["downstream_task_success"] is False
    assert bundle.conditions[1].observations["latency_ms"] == 110
    assert bundle.conditions[1].observations["cost_usd"] == 0.003
    assert bundle.records[0].payload["candidate_metrics"]["cost_usd"] == 0.003
