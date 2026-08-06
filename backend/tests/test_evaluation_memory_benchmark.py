import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentguard.benchmark_evidence import (
    BenchmarkEvidenceRepository,
    recompute_integrity_hash,
)
from agentguard.evaluation_memory import EvaluationKnowledge, EvaluationKnowledgeRepository
from agentguard.release_decision_gate import evaluate_release_decision
from agentguard.store import Store
from agentguard.api import app
from test_project_intelligence import registration


def test_evaluation_knowledge_merges_new_evidence_without_cross_project_leakage(tmp_path: Path) -> None:
    repository = EvaluationKnowledgeRepository(Store(str(tmp_path / "memory.db")))
    first = repository.record(EvaluationKnowledge(
        project_id="agent-a",
        component_pattern="planning_skill",
        common_risks=["constraint violation"],
        recommended_dimensions=["delivery"],
        scenario_templates=["resource constraint"],
        source_evaluation_ids=["eval-1"],
        evidence_refs=["sha256:evidence-1"],
    ))
    merged = repository.record(EvaluationKnowledge(
        project_id="agent-a",
        component_pattern="planning_skill",
        common_risks=["missing information"],
        recommended_dimensions=["boundary"],
        scenario_templates=["preference conflict"],
        source_evaluation_ids=["eval-2"],
        evidence_refs=["sha256:evidence-2"],
        evidence_level="inferred",
    ))

    assert first.knowledge_id == merged.knowledge_id
    assert merged.sample_count == 2
    assert merged.common_risks == ["constraint violation", "missing information"]
    assert merged.evidence_level == "mixed"
    assert [item.component_pattern for item in repository.list("agent-a")] == ["planning_skill"]
    assert repository.list("agent-b") == []


def test_benchmark_import_normalizes_before_after_percentages_and_is_idempotent(tmp_path: Path) -> None:
    repository = BenchmarkEvidenceRepository(Store(str(tmp_path / "benchmark.db")))
    source = json.dumps({
        "benchmark": "custom-eval",
        "before": {"success": "70%", "latency_ms": 100},
        "after": {"success": "75%", "latency_ms": 120},
    }, ensure_ascii=False).encode("utf-8")
    payload = json.loads(source)

    first = repository.import_result(
        "agent-a", payload, source_ref="uploads/custom-eval.json", source_bytes=source
    )
    second = repository.import_result(
        "agent-a", payload, source_ref="uploads/custom-eval.json", source_bytes=source
    )

    assert second == first
    assert {(item.metric_name, item.unit) for item in first.metrics} == {
        ("success", "ratio"), ("latency_ms", "custom")
    }
    success = next(item for item in first.metrics if item.metric_name == "success")
    assert success.baseline_value == pytest.approx(0.70)
    assert success.candidate_value == pytest.approx(0.75)
    assert first.integrity_hash == recompute_integrity_hash(first)


def test_benchmark_import_rejects_missing_comparable_metric(tmp_path: Path) -> None:
    repository = BenchmarkEvidenceRepository(Store(str(tmp_path / "benchmark-invalid.db")))
    with pytest.raises(ValueError, match="shared numeric metric"):
        repository.import_result(
            "agent-a",
            {"benchmark": "custom", "before": {"success": "n/a"}, "after": {"success": "n/a"}},
            source_ref="uploads/invalid.json",
        )


def test_release_gate_blocks_tampered_supplementary_benchmark_evidence() -> None:
    from test_release_decision_gate import _report

    report = _report()
    report.supplementary_evidence = [SimpleNamespace(
        evidence_level="external",
        source_sha256="0" * 64,
        evidence_refs=["benchmark:tampered"],
        integrity_hash="0" * 64,
        project_id="generic-agent",
        benchmark_name="custom",
        source_ref="upload.json",
        metrics=[],
    )]
    result = evaluate_release_decision(report)
    assert result.decision == "block"
    assert any("Supplementary benchmark evidence" in reason for reason in result.blocking_reasons)


def test_api_registration_can_import_optional_benchmark_and_evaluation_knowledge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    client = TestClient(app)
    payload = registration().model_dump(mode="json")
    response = client.post(
        "/api/v1/projects/generic-agent/intelligence",
        json={
            "agent_manifest": payload["agent_manifest"],
            "capabilities": payload["capabilities"],
            "runtime_profile": payload["runtime_profile"],
            "baseline_version": payload["baseline_version"],
            "benchmark_evidence": [{
                "source_ref": "api:custom-eval.json",
                "result": {
                    "benchmark": "custom-eval",
                    "before": {"success": 0.70},
                    "after": {"success": 0.75},
                },
            }],
        },
    )
    assert response.status_code == 200
    assert len(response.json()["benchmark_evidence"]) == 1

    knowledge = client.post(
        "/api/v1/projects/generic-agent/evaluation-knowledge",
        json={
            "component_pattern": "planning_skill",
            "common_risks": ["constraint violation"],
            "recommended_dimensions": ["boundary"],
            "scenario_templates": ["resource constraint"],
            "source_evaluation_ids": ["eval-1"],
            "evidence_refs": ["sha256:evidence-1"],
        },
    )
    assert knowledge.status_code == 200
    assert client.get(
        "/api/v1/projects/generic-agent/evaluation-knowledge?component_pattern=planning_skill"
    ).json()[0]["common_risks"] == ["constraint violation"]
    assert client.get("/api/v1/projects/generic-agent/benchmark-evidence").json()[0]["benchmark_name"] == "custom-eval"
