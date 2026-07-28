import json

from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.service import Service


def test_fixture_prepares_a_traceable_harness_run(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    product = service.fixture()

    prepared = service.prepare_harness_run(product.product_id)

    assert prepared.run.status == "awaiting_evidence"
    assert [handoff.kind for handoff in prepared.handoffs] == [
        "evaluation_scope",
        "evaluation_plan",
        "evidence_request",
        "release_hold",
    ]
    assert prepared.release_decision.status == "pending"
    assert service.store.list("handoff", type(prepared.handoffs[0]), product.product_id)


def test_empty_eval_scope_blocks_release(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    product, _ = service.create("No cases")

    prepared = service.prepare_harness_run(product.product_id)

    assert prepared.run.status == "blocked"
    assert prepared.run.blocked_reason == "No evaluation cases are registered for this product version."
    assert prepared.release_decision.status == "blocked"
    assert prepared.handoffs[-1].from_role == "gatekeeper"


def test_products_persist(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    product, _ = service.create("A")

    assert service.product(product.product_id).name == "A"


def test_cli_json_output_and_lookup_failure(tmp_path, capsys):
    db = str(tmp_path / "agentguard.db")

    assert main(["--db", db, "--format", "json", "fixture", "load", "minimal"]) == 0
    fixture_output = json.loads(capsys.readouterr().out)
    product_id = fixture_output["data"]["product"]["product_id"]
    assert main(["--db", db, "--format", "json", "report", "prepare", "--product-id", product_id]) == 0
    prepared_output = json.loads(capsys.readouterr().out)
    assert prepared_output["data"]["harness_run"]["status"] == "awaiting_evidence"

    assert main(["--db", db, "--format", "json", "report", "prepare", "--product-id", "missing"]) == 2
    error_output = json.loads(capsys.readouterr().out)
    assert error_output["error"]["stage"] == "lookup"


def test_api_prepares_harness_run(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "agentguard.db"))
    client = TestClient(app)

    product_id = client.post("/api/v1/fixtures/minimal").json()["product"]["product_id"]
    response = client.post(f"/api/v1/products/{product_id}/reports")

    assert response.status_code == 200
    assert response.json()["harness_run"]["status"] == "awaiting_evidence"
    assert client.post("/api/v1/products/missing/reports").status_code == 404
