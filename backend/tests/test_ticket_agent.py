import inspect
import json

import pytest
from fastapi.testclient import TestClient

from agentguard import resilient, routing
from agentguard.api import app
from agentguard.cli import main
from agentguard.domain import FailureTicket, HarnessRun, Operation
from agentguard.resilient import InjectedCrash
from agentguard.service import Service
from agentguard.ticket import TICKET_CASE_IDS


EXPECTED_FAILURES = {
    "ticket_duplicate_create": "duplicate_side_effect",
    "ticket_illegal_close": "invalid_state_transition",
    "ticket_unauthorized_assign": "permission_violation",
    "ticket_missing_comment": "missing_required_comment",
    "ticket_wrong_owner": "wrong_owner",
    "ticket_missing_transition": "invalid_state_transition",
    "ticket_retry_duplicate_side_effect": "duplicate_side_effect",
    "ticket_workflow_skips_approval": "approval_bypass",
}


def test_ticket_agent_matrix_uses_shared_evidence_and_release_contracts(tmp_path):
    service = Service(str(tmp_path / "ticket.db"))
    fixture = service.ticket_agent_fixture()

    for case_id, failure_type in EXPECTED_FAILURES.items():
        result = service.start_ticket_agent_run(
            fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, case_id
        )
        assert result.release_decision.status == "blocked"
        assert result.verifications[0].failure_type == failure_type
        assert result.evidence[0].verification_id == result.verifications[0].verification_id
        assert result.findings[0].evidence_ids == [result.evidence[0].evidence_id]
        assert result.tickets[0].finding_id == result.findings[0].finding_id


def test_ticket_agent_clean_baseline_passes_the_same_router_and_gate(tmp_path):
    service = Service(str(tmp_path / "ticket.db"))
    fixture = service.ticket_agent_fixture(faults=[])

    result = service.start_ticket_agent_run(
        fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id,
        "ticket_workflow_skips_approval",
    )

    assert result.release_decision.status == "ready"
    assert result.findings == []
    assert result.tickets == []
    assert result.verifications[0].passed is True


def test_ticket_resume_preserves_one_operation_and_one_failure_ticket(tmp_path):
    db = str(tmp_path / "ticket.db")
    service = Service(db)
    fixture = service.ticket_agent_fixture()
    with pytest.raises(InjectedCrash):
        service.start_ticket_agent_run(
            fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id,
            "ticket_retry_duplicate_side_effect", crash_at="after_runner",
        )
    run_id = service.store.list("harness_run", HarnessRun, fixture.product.product_id)[-1].harness_run_id
    recovered = Service(db).resume_ticket_agent_run(run_id)
    repeated = Service(db).resume_ticket_agent_run(run_id)

    assert recovered.release_decision.status == "blocked"
    assert repeated.release_decision.decision_id == recovered.release_decision.decision_id
    assert len(recovered.operations) == 1
    assert len(recovered.tickets) == 1
    assert recovered.operations[0].status == "completed"
    assert len(Service(db).store.list("failure_ticket", FailureTicket, fixture.product.product_id)) == 1
    assert len(Service(db).store.list("operation", Operation, fixture.product.product_id)) == 1


def test_ticket_cli_and_api_are_closed_loops(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ticket.db")
    monkeypatch.setenv("AGENTGUARD_DB", db)
    assert main(["--db", db, "--format", "json", "fixture", "load", "ticket-agent"]) == 0
    fixture = json.loads(capsys.readouterr().out)["data"]["fixture"]
    assert main([
        "--db", db, "--format", "json", "run", "start-ticket",
        "--product-id", fixture["product"]["product_id"], "--baseline", fixture["baseline"]["version_id"],
        "--candidate", fixture["candidate"]["version_id"], "--case", "ticket_wrong_owner",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["release_decision"]["status"] == "blocked"
    with TestClient(app) as client:
        api_fixture = client.post("/api/v1/fixtures/ticket-agent").json()["fixture"]
        response = client.post("/api/v1/runs/ticket", json={
            "product_id": api_fixture["product"]["product_id"],
            "baseline_version_id": api_fixture["baseline"]["version_id"],
            "candidate_version_id": api_fixture["candidate"]["version_id"],
            "case_id": "ticket_missing_comment",
        })
    assert response.status_code == 200, response.text
    assert response.json()["release_decision"]["status"] == "blocked"


def test_ticket_extension_does_not_add_agent_type_switches_to_core_contracts():
    core_source = "\n".join([inspect.getsource(routing), inspect.getsource(resilient)])
    assert "agent_type" not in core_source
    assert set(EXPECTED_FAILURES) == set(TICKET_CASE_IDS)
