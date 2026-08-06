import pytest
from pydantic import ValidationError

from agentguard.evidence_bundle import ImmutableEvidenceBundle


@pytest.mark.parametrize(
    "evaluation_type",
    ["skill_ablation", "tool_regression", "memory_evolution", "prompt_change", "release_summary"],
)
def test_common_bundle_schema_accepts_each_agent_change_type(evaluation_type: str) -> None:
    bundle = ImmutableEvidenceBundle(
        evaluation_id=f"evaluation-{evaluation_type}",
        project_id="demo",
        evaluation_name="generic evidence test",
        evaluation_type=evaluation_type,
        artifact_manifest_hash="sha256:1234567890abcdef",
        conditions=[
            {
                "condition_id": "condition-1",
                "label": "condition",
                "observations": {"changed": True},
                "evidence_refs": ["evidence-1"],
            }
        ],
        facts=[
            {
                "fact_id": "fact-1",
                "label": "fact",
                "fact_type": "machine_observation",
                "value": {"result": "recorded"},
                "evidence_level": "verified",
                "evidence_refs": ["evidence-1"],
            }
        ],
        records=[
            {
                "record_id": "record-1",
                "record_type": "artifact",
                "source_ref": "evidence-1",
                "payload": {"change_type": evaluation_type},
            }
        ],
        metrics=[{"metric_id": "metric-1", "name": "sample_count", "value": 1, "unit": "count"}],
        type_data={"adapter_payload": {"kind": evaluation_type}},
        integrity={"status": "complete"},
    )
    assert bundle.evaluation_type == evaluation_type
    assert bundle.schema_version == "aig.evidence-bundle.v1"
    with pytest.raises(ValidationError):
        bundle.project_id = "mutated"


def test_common_bundle_rejects_unknown_evaluation_type() -> None:
    with pytest.raises(ValidationError):
        ImmutableEvidenceBundle(
            evaluation_id="evaluation-invalid",
            project_id="demo",
            evaluation_name="invalid",
            evaluation_type="unknown",
            artifact_manifest_hash="sha256:1234567890abcdef",
            conditions=[{"condition_id": "condition-1", "label": "condition", "evidence_refs": ["evidence-1"]}],
            facts=[{"fact_id": "fact-1", "label": "fact", "fact_type": "machine", "evidence_refs": ["evidence-1"]}],
            integrity={"status": "complete"},
        )


def test_evidence_condition_preserves_optional_scenario_identity() -> None:
    bundle = ImmutableEvidenceBundle(
        evaluation_id="evaluation-scenario",
        project_id="demo",
        evaluation_name="scenario test",
        evaluation_type="skill_ablation",
        artifact_manifest_hash="sha256:1234567890abcdef",
        conditions=[
            {
                "condition_id": "condition-1",
                "scenario_id": "scenario_1",
                "label": "condition",
                "evidence_refs": ["evidence-1"],
            }
        ],
        facts=[
            {
                "fact_id": "fact-1",
                "label": "fact",
                "fact_type": "machine",
                "evidence_level": "verified",
                "evidence_refs": ["evidence-1"],
            }
        ],
        integrity={"status": "complete"},
    )
    assert bundle.conditions[0].scenario_id == "scenario_1"
