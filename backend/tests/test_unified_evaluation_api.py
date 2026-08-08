from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import agentguard.api as api_module
from agentguard.domain import ProviderBinding
from agentguard.evaluation_planning import EvaluationPlan
from agentguard.evaluation_request import EvaluationRequest
from agentguard.evaluation_scope import EvaluationScope
from agentguard.service import Service
from agentguard.skill_ablation import execute_skill_ablation_matrix

from test_evaluation_request import _registration
from test_skill_ablation_matrix import SkillRunner, _plan, _readiness


def _zip_package() -> bytes:
    registration = _registration().model_dump(mode="json")
    declaration = {
        "agent_manifest": registration["agent_manifest"],
        "capabilities": registration["capabilities"],
        "runtime_profile": registration["runtime_profile"],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("aig.project.json", json.dumps(declaration, ensure_ascii=False))
    return output.getvalue()


def _binding(project_id: str) -> ProviderBinding:
    return ProviderBinding(
        project_id=project_id,
        role="control_plane",
        provider="vllm",
        base_url="http://127.0.0.1:8000/v1",
        model="local",
        expected_environment_variable="API_TEST_KEY",
        credential_source_ref="test-env",
        batch_budget_usd=0.1,
        timeout_seconds=30,
        allowed_hosts=["127.0.0.1"],
        data_retention_policy="test",
    )


def _scoped_api_plan() -> tuple[EvaluationPlan, EvaluationRequest]:
    source_plan = _plan()
    request = EvaluationRequest(
        request_id="api-evaluation-request",
        project_id="generic-agent",
        component_type="skill",
        component_name="task_planning",
        change_type="remove",
        candidate_version="git:candidate",
        baseline_version="git:baseline",
    )
    scope = EvaluationScope(
        scope_id="a" * 64,
        project_id=request.project_id,
        evaluation_request_id=request.request_id,
        baseline_version=request.baseline_version,
        candidate_version=request.candidate_version,
        baseline_runtime_fingerprint="b" * 64,
        candidate_runtime_fingerprint="c" * 64,
        provider_binding_id="api-binding",
        provider="vllm",
        model="local",
        provider_binding_fingerprint="d" * 64,
        fixture_catalog_fingerprint="e" * 64,
        planned_trial_count=len(source_plan.scenarios) * 3,
        budget_usd=0.1,
        timeout_seconds=30,
        side_effect_policy="isolated_read",
        frozen_at="2026-08-06T00:00:00+00:00",
    )
    return source_plan.model_copy(update={
        "project_id": request.project_id,
        "target_id": "task-planning",
        "component_name": request.component_name,
        "change_id": request.request_id,
        "evaluation_scope": scope,
    }), request


def test_upload_ref_scan_and_provider_binding_list_are_backend_owned(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "api.db"
    upload_root = tmp_path / "uploads"
    monkeypatch.setenv("AGENTGUARD_DB", str(db))
    monkeypatch.setenv("AGENTGUARD_UPLOAD_ROOT", str(upload_root))
    monkeypatch.setenv("API_TEST_KEY", "runtime-secret")
    client = TestClient(api_module.app)

    registration = _registration().model_dump(mode="json")
    assert client.post(
        "/api/v1/projects/generic-agent/intelligence",
        json={
            "agent_manifest": registration["agent_manifest"],
            "capabilities": registration["capabilities"],
            "runtime_profile": registration["runtime_profile"],
            "baseline_version": registration["baseline_version"],
        },
    ).status_code == 200

    uploaded = client.post(
        "/api/v1/projects/generic-agent/uploads",
        files={"file": ("agent.zip", _zip_package(), "application/zip")},
        data={"source_kind": "package"},
    )
    assert uploaded.status_code == 200
    upload = uploaded.json()
    assert upload["source_ref"].startswith("upload://")
    assert "source_path" not in upload
    assert len(upload["source_fingerprint"]) == 64

    scanned = client.post(
        "/api/v1/projects/generic-agent/scan",
        json={
            "source_kind": "package",
            "source_ref": upload["source_ref"],
            "version": "git:candidate",
        },
    )
    assert scanned.status_code == 200
    assert scanned.json()["scan"]["status"] == "ready"

    binding = client.post(
        "/api/v1/projects/generic-agent/provider-bindings",
        json=_binding("generic-agent").model_dump(mode="json", exclude={"project_id", "provider_binding_id", "created_at"}),
    )
    assert binding.status_code == 200
    assert binding.json()["expected_environment_variable"] == "API_TEST_KEY"
    assert "credential_source_ref" not in binding.json()
    listed = client.get("/api/v1/projects/generic-agent/provider-bindings")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "available"
    assert listed.json()[0]["expected_environment_variable"] == "API_TEST_KEY"
    assert "runtime-secret" not in json.dumps(listed.json())


def test_run_status_evidence_and_report_api_share_one_run(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "api.db"
    monkeypatch.setenv("AGENTGUARD_DB", str(db))
    monkeypatch.setenv("API_TEST_KEY", "runtime-secret")
    service = Service(str(db))
    service.register_project_intelligence(_registration())
    plan, request = _scoped_api_plan()
    service.create_evaluation_request(
        request,
        candidate_available=True,
        candidate_component_name=request.component_name,
    )
    service.save_evaluation_plan(plan)
    binding = _binding("generic-agent")
    service.record_provider_binding("generic-agent", binding)

    def fake_execute(plan, intelligence, **kwargs):
        return execute_skill_ablation_matrix(
            plan,
            evaluation_id=kwargs["evaluation_id"],
            readiness=_readiness(plan),
            runner=SkillRunner(),
            run_root=tmp_path / "run-root",
        )

    fake_report = SimpleNamespace(
        report_id="report_api_convergence",
        model_dump=lambda mode="json": {
            "report_id": "report_api_convergence",
            "evidence": {"source": "persisted-report-evidence"},
        },
    )
    monkeypatch.setattr(api_module, "execute_evaluation_run", fake_execute)
    monkeypatch.setattr(api_module, "build_product_evaluation_report", lambda *args, **kwargs: fake_report)
    client = TestClient(api_module.app)

    started = client.post(
        "/api/v1/projects/generic-agent/evaluations/runs",
        json={
            "evaluation_plan_id": plan.plan_id,
            "manifest_path": str(tmp_path / "unused-manifest.json"),
            "cache_root": str(tmp_path / "unused-cache"),
            "run_root": str(tmp_path / "unused-run"),
            "oracle_command": ["unused-oracle"],
            "oracle_id": "api-oracle",
        },
    )
    assert started.status_code == 200
    run = started.json()
    assert run["status"] == "completed"
    assert run["current_stage"] == "evidence"
    assert run["readiness_ref"].startswith("sha256:")
    assert run["matrix_artifact_ref"].startswith("sha256:")
    assert run["evidence_bundle_ref"].startswith("sha256:")

    run_id = run["run_id"]
    assert client.get(f"/api/v1/projects/generic-agent/evaluations/runs/{run_id}").json()["run_id"] == run_id
    assert client.get(f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/status").json()["run_id"] == run_id
    evidence = client.get(f"/api/v1/projects/generic-agent/evaluations/runs/{run_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()["scope_id"] == plan.evaluation_scope.scope_id

    report_request = client.post(
        f"/api/v1/projects/generic-agent/evaluations/runs/{run_id}/report",
        json={
            "run_id": run_id,
            "provider_binding_id": binding.provider_binding_id,
            "product_definition": {
                "component_type": "skill",
                "component_name": "task_planning",
                "description": "Plan the user task.",
                "product_responsibility": "Turn a request into a plan.",
                "user_job": "Obtain a usable plan.",
            },
        },
    )
    assert report_request.status_code == 200, report_request.text
    assert report_request.json()["report_id"] == "report_api_convergence"
    assert client.get(f"/api/v1/projects/generic-agent/evaluations/runs/{run_id}/report").json()["report_id"] == "report_api_convergence"
    assert client.get(f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/report").status_code == 200
    assert client.get(f"/api/v1/projects/generic-agent/reports/report_api_convergence/evidence").json() == {
        "source": "persisted-report-evidence"
    }


def test_server_owned_execution_config_and_request_scoped_run_routes(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "config-api.db"
    monkeypatch.setenv("AGENTGUARD_DB", str(db))
    monkeypatch.setenv("API_TEST_KEY", "runtime-secret")
    app_service = Service(str(db))
    app_service.register_project_intelligence(_registration())
    plan, request = _scoped_api_plan()
    app_service.create_evaluation_request(
        request,
        candidate_available=True,
        candidate_component_name=request.component_name,
    )
    app_service.save_evaluation_plan(plan)
    binding = _binding("generic-agent")
    app_service.record_provider_binding("generic-agent", binding)

    def fake_execute(plan, intelligence, **kwargs):
        return execute_skill_ablation_matrix(
            plan,
            evaluation_id=kwargs["evaluation_id"],
            readiness=_readiness(plan),
            runner=SkillRunner(),
            run_root=tmp_path / "run-root",
        )

    fake_report = SimpleNamespace(
        report_id="report_config_api",
        model_dump=lambda mode="json": {
            "report_id": "report_config_api",
            "evidence": {"source": "persisted-report-evidence"},
        },
    )
    monkeypatch.setattr(api_module, "execute_evaluation_run", fake_execute)
    monkeypatch.setattr(api_module, "build_product_evaluation_report", lambda *args, **kwargs: fake_report)
    client = TestClient(api_module.app)

    projects = client.get("/api/v1/projects")
    assert projects.status_code == 200
    assert projects.json()[0]["project_id"] == "generic-agent"

    config_response = client.post(
        "/api/v1/projects/generic-agent/evaluation-execution-configurations",
        json={
            "name": "test target contract",
            "manifest_path": str(tmp_path / "manifest.json"),
            "cache_root": str(tmp_path / "cache"),
            "run_root_parent": str(tmp_path / "runs"),
            "oracle_command": ["private-oracle", "--contract"],
            "oracle_id": "api-oracle",
        },
    )
    assert config_response.status_code == 200
    config = config_response.json()
    assert config["oracle_id"] == "api-oracle"
    assert "manifest_path" not in config
    assert client.get(
        "/api/v1/projects/generic-agent/evaluation-execution-configurations"
    ).json()[0]["config_id"] == config["config_id"]

    started = client.post(
        f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/runs",
        json={"evaluation_plan_id": plan.plan_id, "execution_config_id": config["config_id"]},
    )
    assert started.status_code == 200, started.text
    run = started.json()
    run_id = run["run_id"]
    assert run["execution_config_id"] == config["config_id"]
    assert [event["stage"] for event in run["events"]] == ["execution", "evidence"]
    assert client.get(
        f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/runs"
    ).json()[0]["run_id"] == run_id
    assert client.get(
        f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/runs/{run_id}"
    ).json()["run_id"] == run_id
    assert len(client.get(
        f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/runs/{run_id}/events"
    ).json()) == 2
    assert client.get(
        f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/runs/{run_id}/matrix"
    ).status_code == 200
    assert client.get(
        f"/api/v1/projects/generic-agent/evaluations/{request.request_id}/runs/{run_id}/evidence"
    ).status_code == 200

    report_response = client.post(
        f"/api/v1/projects/generic-agent/evaluations/runs/{run_id}/report",
        json={
            "run_id": run_id,
            "provider_binding_id": binding.provider_binding_id,
            "product_definition": {
                "component_type": "skill",
                "component_name": "task_planning",
                "description": "Plan the user task.",
                "product_responsibility": "Turn a request into a plan.",
                "user_job": "Obtain a usable plan.",
            },
        },
    )
    assert report_response.status_code == 200, report_response.text
    listed_reports = client.get("/api/v1/projects/generic-agent/reports")
    assert listed_reports.status_code == 200
    assert listed_reports.json()[0]["report_id"] == "report_config_api"


def test_imported_report_is_server_persisted_and_exportable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "report-import.db"))
    report_path = Path(__file__).parents[2] / "examples" / "reports" / "lighttable-product-evaluation.zh-CN" / "product-evaluation-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    client = TestClient(api_module.app)

    imported = client.post(
        "/api/v1/projects/lighttable-stage7/reports",
        json={"report": report},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["report"]["report_id"] == report["report_id"]
    listed = client.get("/api/v1/projects/lighttable-stage7/reports")
    assert listed.status_code == 200
    assert listed.json()[0]["source"] == "import"
    assert client.get(
        f"/api/v1/projects/lighttable-stage7/reports/{report['report_id']}"
    ).status_code == 200
    assert client.get(
        f"/api/v1/projects/lighttable-stage7/reports/{report['report_id']}/evidence"
    ).status_code == 200
    for format_name, media_type in (("json", "application/json"), ("md", "text/markdown"), ("html", "text/html")):
        exported = client.get(
            f"/api/v1/projects/lighttable-stage7/reports/{report['report_id']}/export?format={format_name}"
        )
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith(media_type)
