import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.domain import (
    EnvironmentCheck,
    HistoricalReplayEvidence,
    MemoryDependency,
    MemoryEntry,
    NativeHarnessContract,
    RuntimeEnvironmentContract,
    RuntimeEnvironmentPreflight,
    TaskVerifierContract,
)
from agentguard.evolution import EvolutionIntakeError, audit_intake_report_claims
from agentguard.service import Service


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def revision_pair(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "real-agent"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "eval@example.test")
    git(repo, "config", "user.name", "Eval Test")
    (repo / "agent.py").write_text("def run(task):\n    return 'baseline'\n", encoding="utf-8")
    (repo / "requirements.lock").write_text("demo==1.0\n", encoding="utf-8")
    (repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    baseline = git(repo, "rev-parse", "HEAD")
    (repo / "agent.py").write_text("def run(task):\n    return 'candidate'\n", encoding="utf-8")
    git(repo, "add", "agent.py")
    git(repo, "commit", "-m", "candidate")
    return repo, baseline, git(repo, "rev-parse", "HEAD")


def test_stage6a_static_intake_preserves_git_identity_and_never_claims_live_execution(tmp_path):
    repo, baseline_commit, candidate_commit = revision_pair(tmp_path)
    service = Service(str(tmp_path / "agentguard.db"))
    project, _ = service.create("External Agent")

    result = service.intake_agent_evolution(
        project.product_id,
        repo,
        baseline_commit,
        candidate_commit,
        repository_url="https://example.test/owner/real-agent",
        declared_entrypoint="agent.run",
    )

    assert result.baseline.commit_sha == baseline_commit
    assert result.candidate.commit_sha == candidate_commit
    assert result.baseline.lock_files == ["requirements.lock"]
    assert result.case.status == "awaiting_approval"
    assert result.comparison.status == "awaiting_evidence"
    assert result.report.quality_status == "PASS"
    assert result.changeset.changes[0].path == "agent.py"
    assert result.changeset.changes[0].review_status == "review_required"
    assert all(item.status == "preflight_ready" for item in result.parity)


def test_stage6a_memory_stale_propagation_is_project_scoped_and_creates_review_work(tmp_path):
    repo, baseline_commit, candidate_commit = revision_pair(tmp_path)
    service = Service(str(tmp_path / "agentguard.db"))
    project, _ = service.create("External Agent")
    other, _ = service.create("Other Agent")
    result = service.intake_agent_evolution(project.product_id, repo, baseline_commit, candidate_commit)
    memory = service.record_memory_entry(
        project.product_id,
        MemoryEntry(
            project_id=project.product_id,
            kind="fact",
            content="The Agent returns baseline content.",
            evidence_level="verified",
            evidence_refs=[f"git:{baseline_commit}:agent.py"],
            status="verified",
        ),
    )
    service.record_memory_dependency(
        project.product_id,
        MemoryDependency(
            project_id=project.product_id,
            memory_id=memory.memory_id,
            dependent_kind="agent_revision",
            dependent_id=result.baseline.revision_id,
            component_paths=["agent.py"],
        ),
    )

    propagation = service.propagate_evolution_stale(project.product_id, result.changeset.evolution_changeset_id)

    stored = service.store.get("memory_entry", memory.memory_id, MemoryEntry)
    assert propagation.stale_memory_ids == [memory.memory_id]
    assert len(propagation.review_work_item_ids) == 1
    assert stored and stored.status == "stale"
    with pytest.raises(EvolutionIntakeError):
        service.propagate_evolution_stale(other.product_id, result.changeset.evolution_changeset_id)


def test_stage6a_cli_api_and_report_quality_gate(tmp_path, capsys, monkeypatch):
    repo, baseline_commit, candidate_commit = revision_pair(tmp_path)
    db = str(tmp_path / "agentguard.db")
    service = Service(db)
    project, _ = service.create("External Agent")
    assert main([
        "--db", db, "--format", "json", "evolution", "intake",
        "--project-id", project.product_id, "--source", str(repo),
        "--baseline", baseline_commit, "--candidate", candidate_commit,
    ]) == 0
    cli_result = json.loads(capsys.readouterr().out)["data"]
    report_id = cli_result["intake_review_report"]["intake_review_report_id"]
    assert main([
        "--db", db, "--format", "json", "evolution", "report",
        "--project-id", project.product_id, "--report-id", report_id,
    ]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["quality_status"] == "PASS"

    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    api_service = Service(str(tmp_path / "api.db"))
    api_project, _ = api_service.create("API External Agent")
    client = TestClient(app)
    response = client.post(
        f"/api/v1/products/{api_project.product_id}/evolution/intake",
        json={
            "source": str(repo), "baseline_ref": baseline_commit, "candidate_ref": candidate_commit,
            "declared_entrypoint": "agent.run",
        },
    )
    assert response.status_code == 200
    api_report = response.json()["intake_review_report"]
    assert api_report["quality_status"] == "PASS"
    api_case_id = response.json()["evolution_case"]["evolution_case_id"]
    admission = client.post(
        f"/api/v1/products/{api_project.product_id}/evolution/{api_case_id}/admission"
    )
    assert admission.status_code == 200
    assert admission.json()["evaluation_admission"]["level"] == "L0_artifact_only"
    invalid_environment = client.post(
        f"/api/v1/products/{api_project.product_id}/evolution/{api_case_id}/environment-contract",
        json={"docker_ref": "git:Dockerfile", "status": "approved"},
    )
    assert invalid_environment.status_code == 422
    other, _ = api_service.create("Other API Agent")
    assert client.get(
        f"/api/v1/products/{other.product_id}/evolution/reports/{api_report['intake_review_report_id']}"
    ).status_code == 404

    bad_claim = service.evolution_report(project.product_id, report_id).claims[0].model_copy(update={"evidence_refs": ["git:wrong"]})
    assert audit_intake_report_claims([bad_claim], {baseline_commit, candidate_commit})


def test_stage6a_admission_levels_detect_environment_failure_and_queue_only_level2(tmp_path, capsys):
    repo, baseline_commit, candidate_commit = revision_pair(tmp_path)
    db = str(tmp_path / "agentguard.db")
    service = Service(db)
    project, _ = service.create("External Agent")
    intake = service.intake_agent_evolution(project.product_id, repo, baseline_commit, candidate_commit)
    case_id = intake.case.evolution_case_id

    l0 = service.assess_evolution_admission(project.product_id, case_id)
    assert l0.admission.level == "L0_artifact_only"
    assert l0.admission.allowed_operations == ["changeset_analysis"]
    assert l0.pipeline is None

    for revision_id, marker in ((intake.baseline.revision_id, "a"), (intake.candidate.revision_id, "b")):
        service.record_historical_replay_evidence(
            project.product_id,
            HistoricalReplayEvidence(
                project_id=project.product_id,
                evolution_case_id=case_id,
                revision_id=revision_id,
                trace_sha256=marker * 64,
                tool_result_sha256="c" * 64,
                execution_log_sha256="d" * 64,
                initial_state_sha256="e" * 64,
                verifier_evidence_ref=f"artifact:{marker}-verifier",
            ),
        )
    l1 = service.assess_evolution_admission(project.product_id, case_id)
    assert l1.admission.level == "L1_replay"
    assert l1.admission.status == "replay_ready"
    assert "replay" in l1.admission.allowed_operations

    environment = service.record_runtime_environment_contract(
        project.product_id,
        RuntimeEnvironmentContract(
            project_id=project.product_id,
            evolution_case_id=case_id,
            docker_ref="git:docker-compose.yml",
            dependency_lock_ref="git:requirements.lock",
            model_config_ref="git:model.toml",
            tools_manifest_ref="git:tools.json",
            reset_command_ref="contract:reset-v1",
            initial_state_ref="artifact:seed-v1",
            status="approved",
        ),
    )
    failed = service.record_runtime_preflight(
        project.product_id,
        RuntimeEnvironmentPreflight(
            project_id=project.product_id,
            evolution_case_id=case_id,
            environment_contract_id=environment.runtime_environment_contract_id,
            checks=[
                EnvironmentCheck(name=name, status="failed" if name == "docker" else "passed", detail=f"{name} check")
                for name in ("docker", "dependency", "model_config", "tools", "reset", "initial_state", "verifier")
            ],
        ),
    )
    assert failed.status == "environment_not_satisfied"
    blocked_runtime = service.assess_evolution_admission(project.product_id, case_id)
    assert blocked_runtime.admission.level == "L1_replay"
    assert blocked_runtime.admission.status == "environment_not_satisfied"
    assert blocked_runtime.pipeline is None

    service.record_native_harness_contract(
        project.product_id,
        NativeHarnessContract(
            project_id=project.product_id,
            evolution_case_id=case_id,
            baseline_entrypoint="agent.run",
            candidate_entrypoint="agent.run",
            adapter_ref="native:docker-jsonl",
            trace_schema_ref="contract:trace-v1",
            behavior_mode="production_parity",
            status="approved",
        ),
    )
    service.record_task_verifier_contract(
        project.product_id,
        TaskVerifierContract(
            project_id=project.product_id,
            evolution_case_id=case_id,
            task_spec_ref="contract:task-v1",
            verifier_ref="contract:independent-verifier-v1",
            pass_iff="independent verifier returns pass",
            initial_state_ref="artifact:seed-v1",
            trace_evidence_ref="contract:trace-v1",
            status="approved",
        ),
    )
    passed = service.record_runtime_preflight(
        project.product_id,
        RuntimeEnvironmentPreflight(
            project_id=project.product_id,
            evolution_case_id=case_id,
            environment_contract_id=environment.runtime_environment_contract_id,
            checks=[
                EnvironmentCheck(name=name, status="passed", evidence_ref=f"artifact:{name}-ok", detail=f"{name} check")
                for name in ("docker", "dependency", "model_config", "tools", "reset", "initial_state", "verifier")
            ],
        ),
    )
    assert passed.status == "passed" and passed.environment_fingerprint
    l2 = service.assess_evolution_admission(project.product_id, case_id)
    assert l2.admission.level == "L2_full_runtime"
    assert l2.admission.status == "runtime_ready"
    assert l2.pipeline and l2.pipeline.status == "queued"
    assert l2.pipeline.stages[-3:] == ["skill_ablation", "compare", "report_contract"]

    assert main([
        "--db", db, "--format", "json", "evolution", "assess",
        "--project-id", project.product_id, "--case-id", case_id,
    ]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["evaluation_pipeline"]["status"] == "queued"
