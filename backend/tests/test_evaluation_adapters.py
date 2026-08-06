from dataclasses import dataclass
import pytest

from agentguard.evaluation_adapters import (
    AdapterContext,
    EvaluationAdapterError,
    EvaluationAdapterLayer,
    ImmutableEvidenceBundle,
)
from agentguard.product_reporting import SkillAblationArtifact
from agentguard.skill_ablation_adapter import SkillAblationEvaluationAdapter, build_default_evaluation_adapter_layer


@dataclass(frozen=True)
class Bundle:
    evaluation_type: str
    artifact_manifest_hash: str


class SkillAdapter:
    evaluation_type = "skill_ablation"

    def adapt(self, artifact, *, context):
        return ImmutableEvidenceBundle(
            evaluation_id="evaluation-test",
            project_id=context.project_id,
            evaluation_name=context.evaluation_name,
            evaluation_type="skill_ablation",
            artifact_manifest_hash="sha256:skill-manifest",
            conditions=[{"condition_id": "condition-test", "label": "condition", "evidence_refs": ["ref-test"]}],
            facts=[{"fact_id": "fact-test", "label": "fact", "fact_type": "test", "evidence_level": "verified", "evidence_refs": ["ref-test"]}],
            integrity={"status": "complete"},
        )


def _context(evaluation_type: str = "skill_ablation") -> AdapterContext:
    return AdapterContext(
        project_id="lighttable",
        evaluation_name="Skill Ablation",
        evaluation_type=evaluation_type,
        source_ref="artifact:lighttable",
    )


def test_adapter_context_can_bind_execution_artifacts_to_planned_scenarios() -> None:
    context = _context()
    context.scenario_ids_by_trial_ref["enabled-1"] = "scenario_1"
    assert context.scenario_ids_by_trial_ref == {"enabled-1": "scenario_1"}


def test_adapter_layer_registers_and_dispatches_by_evaluation_type() -> None:
    layer = EvaluationAdapterLayer()
    layer.register(SkillAdapter())
    result = layer.adapt("skill_ablation", {"artifact": True}, context=_context())
    assert result.evaluation_type == "skill_ablation"
    assert isinstance(result, ImmutableEvidenceBundle)
    assert layer.registered_types() == ("skill_ablation",)


def test_adapter_layer_rejects_duplicates_unknown_types_and_context_mismatch() -> None:
    layer = EvaluationAdapterLayer()
    layer.register(SkillAdapter())
    with pytest.raises(EvaluationAdapterError, match="already registered"):
        layer.register(SkillAdapter())
    with pytest.raises(EvaluationAdapterError, match="No Evaluation Adapter"):
        layer.adapter_for("tool_regression")
    with pytest.raises(EvaluationAdapterError, match="does not match"):
        layer.adapt("skill_ablation", {}, context=_context("tool_regression"))


def test_skill_ablation_adapter_is_registered_and_preserves_existing_bundle() -> None:
    layer = build_default_evaluation_adapter_layer()
    assert isinstance(layer.adapter_for("skill_ablation"), SkillAblationEvaluationAdapter)
    assert layer.registered_types() == ("skill_ablation",)
    # The type check is deliberately exercised before touching the legacy
    # parser: a malformed artifact must fail at the adapter boundary.
    with pytest.raises(TypeError, match="unsupported artifact type"):
        layer.adapt("skill_ablation", [object()], context=_context())
