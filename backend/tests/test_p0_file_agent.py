import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.domain import ComponentSnapshot
from agentguard.service import Service


FIXTURES = Path(__file__).parents[1] / "fixtures" / "file_agent"


def test_import_versions_creates_snapshots_and_changeset(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    product, _ = service.create("File Agent")
    baseline = service.import_version(product.product_id, FIXTURES / "v1", "v1")
    candidate = service.import_version(product.product_id, FIXTURES / "v2", "v2")

    changeset = service.compare_versions(product.product_id, baseline.version_id, candidate.version_id)

    assert changeset.baseline_version_id == baseline.version_id
    assert changeset.candidate_version_id == candidate.version_id
    assert {change.kind for change in changeset.changes} == {
        "permission_changed",
        "tool_capability_expanded",
        "skill_changed",
    }
    assert all(snapshot.fingerprint for snapshot in service.store.list("snapshot", type(changeset.baseline_snapshot), product.product_id))


def test_p0_file_agent_blocks_unauthorized_write_with_evidence_chain(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_agent_fixture()

    result = service.run_file_agent(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
    )

    assert result.run.status == "blocked"
    assert result.eval_plan.selected_case_ids == [
        "eval_normal_write",
        "eval_security_no_secret_write",
        "eval_smoke",
    ]
    assert any(item.eval_case_id == "eval_security_no_secret_write" for item in result.work_items)
    assert result.work_items[0].owner == "runner"
    assert any(call.path == "secrets/leak.txt" for execution in result.executions for call in execution.tool_calls)
    assert result.verifications[1].passed is False
    assert result.verifications[1].severity == "critical"
    assert result.evidence[1].level == "verified"
    assert result.findings[0].evidence_ids == [result.evidence[1].evidence_id]
    assert result.release_decision.status == "blocked"
    assert result.release_decision.finding_ids == [result.findings[0].finding_id]
    assert [event.event_type for event in result.events] == [
        "RUN_CREATED",
        "PLAN_CREATED",
        "TRIALS_COMPLETED",
        "VERIFICATION_COMPLETED",
        "FINDING_CREATED",
        "RELEASE_DECIDED",
        "RUN_RECORDED",
    ]


def test_same_baseline_has_no_security_regression(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_agent_fixture()

    result = service.run_file_agent(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.baseline.version_id,
    )

    assert result.run.status == "recorded"
    assert result.release_decision.status == "ready"
    assert result.findings == []


def test_cli_and_api_exercise_file_agent_closed_loop(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "agentguard.db")
    assert main(["--db", db, "--format", "json", "fixture", "load", "file-agent"]) == 0
    fixture = json.loads(capsys.readouterr().out)["data"]["fixture"]

    assert main(
        [
            "--db",
            db,
            "--format",
            "json",
            "run",
            "start",
            "--product-id",
            fixture["product"]["product_id"],
            "--baseline",
            fixture["baseline"]["version_id"],
            "--candidate",
            fixture["candidate"]["version_id"],
        ]
    ) == 0
    cli_output = json.loads(capsys.readouterr().out)
    assert cli_output["data"]["release_decision"]["status"] == "blocked"

    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    client = TestClient(app)
    api_fixture = client.post("/api/v1/fixtures/file-agent").json()["fixture"]
    response = client.post(
        "/api/v1/runs",
        json={
            "product_id": api_fixture["product"]["product_id"],
            "baseline_version_id": api_fixture["baseline"]["version_id"],
            "candidate_version_id": api_fixture["candidate"]["version_id"],
        },
    )
    assert response.status_code == 200
    assert response.json()["release_decision"]["status"] == "blocked"


def test_cli_imports_two_versions_and_persists_snapshots(tmp_path, capsys):
    db = str(tmp_path / "agentguard.db")
    assert main(["--db", db, "--format", "json", "product", "add", "--name", "File Agent"]) == 0
    product_id = json.loads(capsys.readouterr().out)["data"]["product"]["product_id"]

    versions = []
    for label in ("v1", "v2"):
        assert main(
            [
                "--db", db, "--format", "json", "version", "import", "--product-id", product_id,
                "--source", str(FIXTURES / label), "--label", label,
            ]
        ) == 0
        versions.append(json.loads(capsys.readouterr().out)["data"]["version"]["version_id"])

    snapshots = Service(db).store.list("snapshot", ComponentSnapshot, product_id)
    assert {snapshot.version_id for snapshot in snapshots} == set(versions)
