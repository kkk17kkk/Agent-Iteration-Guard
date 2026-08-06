"""Validation contract for the v1.0 Tool Regression evidence shape."""

from __future__ import annotations

from collections.abc import Mapping
import math


class ToolRegressionValidationError(ValueError):
    """Raised when a Tool Regression artifact lacks independent evidence."""


_BOOLEAN_METRICS = ("tool_call_success", "argument_correct", "downstream_task_success")
_NUMERIC_METRICS = ("latency_ms", "cost_usd")


def _validate_condition_trace(artifact: Mapping[str, object], condition: str) -> list[Mapping[str, object]]:
    trace = artifact.get(f"{condition}_trace")
    if not isinstance(trace, list) or not trace or any(not isinstance(item, Mapping) for item in trace):
        raise ToolRegressionValidationError(
            f"E_TOOL_TRACE_MISSING: {condition}_trace must contain at least one structured trace event."
        )
    if any(not isinstance(item.get("event_type"), str) or not str(item["event_type"]).strip() for item in trace):
        raise ToolRegressionValidationError(
            f"E_TOOL_TRACE_INVALID: {condition}_trace events require event_type."
        )
    return trace


def validate_tool_regression_artifact(
    artifact: Mapping[str, object],
    *,
    expected_tool_name: str | None = None,
) -> None:
    tool_name = artifact.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ToolRegressionValidationError("E_TOOL_NAME_REQUIRED: Tool Regression requires tool_name.")
    if expected_tool_name is not None and tool_name != expected_tool_name:
        raise ToolRegressionValidationError(
            f"E_TOOL_COMPONENT_MISMATCH: artifact targets {tool_name}, not {expected_tool_name}."
        )

    for condition in ("baseline", "candidate"):
        metrics = artifact.get(f"{condition}_metrics")
        if not isinstance(metrics, Mapping):
            raise ToolRegressionValidationError(
                f"E_TOOL_METRICS_MISSING: {condition}_metrics is required."
            )
        for name in _BOOLEAN_METRICS:
            if not isinstance(metrics.get(name), bool):
                raise ToolRegressionValidationError(
                    f"E_TOOL_METRIC_INVALID: {condition}_metrics.{name} must be boolean."
                )
        for name in _NUMERIC_METRICS:
            value = metrics.get(name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ToolRegressionValidationError(
                    f"E_TOOL_METRIC_INVALID: {condition}_metrics.{name} must be a non-negative number."
                )
        _validate_condition_trace(artifact, condition)
        if f"{condition}_output" not in artifact:
            raise ToolRegressionValidationError(
                f"E_TOOL_OUTPUT_MISSING: {condition}_output is required."
            )

    oracle = artifact.get("oracle")
    if not isinstance(oracle, Mapping) or oracle.get("status") != "verified":
        raise ToolRegressionValidationError(
            "E_TOOL_ORACLE_UNVERIFIED: Tool Regression requires a verified independent oracle."
        )
    oracle_refs = oracle.get("evidence_refs")
    if not isinstance(oracle_refs, list) or not oracle_refs or any(not isinstance(ref, str) or not ref for ref in oracle_refs):
        raise ToolRegressionValidationError(
            "E_TOOL_ORACLE_EVIDENCE_MISSING: verified oracle must provide evidence_refs."
        )


def tool_condition_observations(artifact: Mapping[str, object], condition: str) -> dict[str, object]:
    metrics = artifact.get(f"{condition}_metrics")
    if not isinstance(metrics, Mapping):
        raise ToolRegressionValidationError(f"E_TOOL_METRICS_MISSING: {condition}_metrics is required.")
    trace = _validate_condition_trace(artifact, condition)
    return {
        **{name: metrics[name] for name in (*_BOOLEAN_METRICS, *_NUMERIC_METRICS)},
        "trace_event_count": len(trace),
        "output_recorded": f"{condition}_output" in artifact,
        "oracle_verified": True,
    }


__all__ = [
    "ToolRegressionValidationError",
    "tool_condition_observations",
    "validate_tool_regression_artifact",
]
