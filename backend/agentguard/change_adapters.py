"""Boundary adapters for the three evaluation types supported by AIG v1."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from .evaluation_adapters import AdapterContext, EvaluationAdapterLayer
from .evaluation_matrix_adapter import EvaluationMatrixEvidenceAdapter
from .evidence_bundle import (
    EvidenceCondition,
    EvidenceFact,
    EvidenceIntegrity,
    EvidenceMetric,
    EvidenceRecord,
    ImmutableEvidenceBundle,
)
from .interaction_matrix import PAIR_INTERACTION_CONDITIONS
from .skill_ablation_adapter import SkillAblationEvaluationAdapter
from .tool_regression import tool_condition_observations, validate_tool_regression_artifact


def _required_text(artifact: Mapping[str, object], key: str) -> str:
    value = artifact.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required for {artifact.get('evaluation_type', 'change')} evaluation artifact.")
    return value


def _required_mapping(artifact: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = artifact.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object in a change evaluation artifact.")
    return value


def _required_refs(artifact: Mapping[str, object]) -> list[str]:
    refs = artifact.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs):
        raise ValueError("change evaluation artifact requires non-empty evidence_refs.")
    return [_opaque_ref(ref) for ref in refs]


def _bundle(
    *,
    artifact: Mapping[str, object],
    context: AdapterContext,
    conditions: list[EvidenceCondition],
    facts: list[EvidenceFact],
    records: list[EvidenceRecord],
    type_data: dict[str, object],
) -> ImmutableEvidenceBundle:
    metrics_raw = artifact.get("metrics", {})
    if not isinstance(metrics_raw, Mapping):
        raise ValueError("metrics must be an object in a change evaluation artifact.")
    integrity_raw = artifact.get("integrity", {"status": "complete"})
    if not isinstance(integrity_raw, Mapping):
        raise ValueError("integrity must be an object in a change evaluation artifact.")
    manifest = _required_text(artifact, "artifact_manifest_hash")
    if len(manifest) < 16:
        raise ValueError("artifact_manifest_hash must be content-addressed and at least 16 characters.")
    return ImmutableEvidenceBundle(
        evaluation_id=_required_text(artifact, "evaluation_id"),
        project_id=context.project_id,
        evaluation_name=context.evaluation_name,
        evaluation_type=context.evaluation_type,
        evaluation_request_id=context.evaluation_request_id,
        baseline_version=context.baseline_version,
        candidate_version=context.candidate_version,
        scope_id=context.scope_id,
        evaluation_plan_id=context.evaluation_plan_id,
        artifact_manifest_hash=manifest,
        conditions=conditions,
        facts=facts,
        records=records,
        metrics=[
            EvidenceMetric(metric_id=_opaque_id("metric", str(key)), name=str(key), value=value, unit="reported")
            for key, value in metrics_raw.items()
            if isinstance(value, (int, float, str))
        ],
        summary={key: value for key, value in metrics_raw.items() if isinstance(value, (int, float, str))},
        type_data=type_data,
        integrity=EvidenceIntegrity.model_validate(integrity_raw),
    )


class ToolRegressionEvaluationAdapter:
    """Normalize the verified v1 Tool baseline/candidate artifact."""

    evaluation_type = "tool_regression"

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle:
        if context.evaluation_type != self.evaluation_type:
            raise ValueError("Tool Regression adapter context type mismatch.")
        tool_name = _required_text(artifact, "tool_name")
        baseline_output = _required_mapping(artifact, "baseline_output")
        candidate_output = _required_mapping(artifact, "candidate_output")
        refs = _required_refs(artifact)
        baseline = {"output_recorded": bool(baseline_output)}
        candidate = {"output_recorded": bool(candidate_output)}
        if "baseline_metrics" in artifact or "candidate_metrics" in artifact:
            validate_tool_regression_artifact(artifact, expected_tool_name=tool_name)
            baseline = tool_condition_observations(artifact, "baseline")
            candidate = tool_condition_observations(artifact, "candidate")
        conditions = [
            EvidenceCondition(
                condition_id=_opaque_id("condition", "tool:baseline"),
                label="保留 Tool 实现测试",
                observations=baseline,
                evidence_refs=refs,
            ),
            EvidenceCondition(
                condition_id=_opaque_id("condition", "tool:candidate"),
                label="替换 Tool 实现测试",
                observations=candidate,
                evidence_refs=refs,
            ),
        ]
        facts = [EvidenceFact(
            fact_id=_opaque_id("fact", tool_name),
            label="Tool 行为观察",
            fact_type="tool_behavior",
            value={"tool_name": tool_name, "baseline": baseline, "candidate": candidate},
            evidence_level="verified",
            evidence_refs=refs,
        )]
        records = [EvidenceRecord(
            record_id=_opaque_id("record", f"tool:{tool_name}"),
            record_type="artifact",
            source_ref=_opaque_ref(f"tool:{tool_name}"),
            payload={
                "tool_name": tool_name,
                "baseline_output": dict(baseline_output),
                "candidate_output": dict(candidate_output),
                **({"baseline_metrics": baseline, "candidate_metrics": candidate} if "baseline_metrics" in artifact or "candidate_metrics" in artifact else {}),
            },
            evidence_refs=refs,
        )]
        return _bundle(
            artifact=artifact,
            context=context,
            conditions=conditions,
            facts=facts,
            records=records,
            type_data={"tool_name": tool_name},
        )


class SkillPairEvaluationAdapter(EvaluationMatrixEvidenceAdapter):
    """Use the shared matrix evidence contract for A-only/B-only/A+B runs."""

    def __init__(self) -> None:
        super().__init__(
            "skill_pair_evaluation",
            PAIR_INTERACTION_CONDITIONS,
            fact_type="interaction_behavior",
            require_live_provider_metadata=False,
        )


def build_v1_evaluation_adapter_layer() -> EvaluationAdapterLayer:
    layer = EvaluationAdapterLayer()
    for adapter in (SkillAblationEvaluationAdapter(), SkillPairEvaluationAdapter(), ToolRegressionEvaluationAdapter()):
        layer.register(adapter)
    return layer


def _opaque_ref(raw_ref: str) -> str:
    return "evidence_" + hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()[:16]


def _opaque_id(prefix: str, raw_value: str) -> str:
    return prefix + "_" + hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "SkillPairEvaluationAdapter",
    "ToolRegressionEvaluationAdapter",
    "build_v1_evaluation_adapter_layer",
]
