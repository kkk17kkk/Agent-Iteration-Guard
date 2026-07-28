import json

import pytest
from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.domain import ComponentSnapshot, FailureTicket, HarnessRun, Operation, RunCheckpoint, ToolPolicy, WorkItem
from agentguard.resilient import InjectedCrash
from agentguard.runner import LocalFileRunner
from agentguard.service import Service


def create_fixture(service: Service):
    fixture = service.file_management_fixture()
    return fixture, fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id


def test_real_file_management_runner_blocks_delete_and_creates_failure_ticket(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    _, product_id, baseline_id, candidate_id = create_fixture(service)

    result = service.start_file_management_run(product_id, baseline_id, candidate_id)

    assert result.run.status == "blocked"
    assert result.run.thread_id == result.run.harness_run_id
    assert result.release_decision.status == "blocked"
    assert [call.tool_name for call in result.executions[0].tool_calls] == [
        "read_file", "write_file", "delete_file",
    ]
    assert result.executions[0].tool_calls[-1].policy_decision == "denied"
    assert result.executions[0].environment_ref == "temporary-file-management-sandbox"
    assert result.executions[0].output_fingerprint
    assert result.verifications[0].failure_type == "permission_violation"
    assert result.tickets[0].finding_id == result.findings[0].finding_id
    assert result.operations[0].status == "completed"
    assert result.operations[0].tool_call_count == 3
    assert max(result.checkpoints, key=lambda item: item.event_sequence).next_step == "completed"


@pytest.mark.parametrize("crash_at", ["before_execute", "after_runner", "after_finding"])
def test_resume_recovers_same_run_without_duplicate_operation(tmp_path, crash_at):
    db = str(tmp_path / "agentguard.db")
    service = Service(db)
    _, product_id, baseline_id, candidate_id = create_fixture(service)

    with pytest.raises(InjectedCrash):
        service.start_file_management_run(product_id, baseline_id, candidate_id, crash_at=crash_at)

    run = service.store.list("harness_run", HarnessRun, product_id)[0]
    operations_before = service.store.list("operation", Operation, product_id)
    tickets_before = service.store.list("failure_ticket", FailureTicket, product_id)

    recovered = Service(db).resume_file_management_run(run.harness_run_id)
    resumed_again = Service(db).resume_file_management_run(run.harness_run_id)

    assert recovered.run.harness_run_id == run.harness_run_id
    assert resumed_again.run.harness_run_id == run.harness_run_id
    assert recovered.release_decision.status == "blocked"
    assert len(resumed_again.operations) == 1
    assert resumed_again.operations[0].tool_call_count == 3
    assert len(resumed_again.tickets) == 1
    if crash_at == "before_execute":
        assert operations_before == []
    if crash_at == "after_runner":
        assert operations_before[0].operation_id == resumed_again.operations[0].operation_id
        assert operations_before[0].execution_id == resumed_again.operations[0].execution_id
    if crash_at == "after_finding":
        assert tickets_before[0].ticket_id == resumed_again.tickets[0].ticket_id


def test_checkpoint_records_the_next_durable_step(tmp_path):
    db = str(tmp_path / "agentguard.db")
    service = Service(db)
    _, product_id, baseline_id, candidate_id = create_fixture(service)

    with pytest.raises(InjectedCrash, match="before the runner"):
        service.start_file_management_run(product_id, baseline_id, candidate_id, crash_at="before_execute")

    run = service.store.list("harness_run", HarnessRun, product_id)[0]
    checkpoint = max(
        [item for item in service.store.list("checkpoint", RunCheckpoint, product_id) if item.harness_run_id == run.harness_run_id],
        key=lambda item: item.event_sequence,
    )
    assert checkpoint.next_step == "execute"


def test_interrupted_operation_is_a_runner_failure_not_an_agent_regression(tmp_path):
    db = str(tmp_path / "agentguard.db")
    service = Service(db)
    _, product_id, baseline_id, candidate_id = create_fixture(service)

    with pytest.raises(InjectedCrash):
        service.start_file_management_run(product_id, baseline_id, candidate_id, crash_at="before_execute")

    run = service.store.list("harness_run", HarnessRun, product_id)[0]
    work_item = [item for item in service.store.list("work_item", WorkItem, product_id) if item.harness_run_id == run.harness_run_id][0]
    snapshot = next(
        item
        for item in service.store.list("snapshot", ComponentSnapshot, product_id)
        if item.version_id == candidate_id
    )
    operation_id = LocalFileRunner.operation_id(run, work_item, snapshot)
    service.store.save("operation", operation_id, product_id, Operation(
        operation_id=operation_id,
        harness_run_id=run.harness_run_id,
        work_item_id=work_item.work_item_id,
        input_hash=snapshot.fingerprint,
        status="running",
    ))

    result = Service(db).resume_file_management_run(run.harness_run_id)

    assert result.run.status == "failed"
    assert result.run.blocked_reason == "runner_interrupted"
    assert result.findings == []
    assert result.release_decision is None


def test_two_runs_for_one_product_keep_policy_and_changeset_scoped_to_the_run(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    _, product_id, baseline_id, candidate_id = create_fixture(service)

    first = service.start_file_management_run(product_id, baseline_id, candidate_id)
    second = service.start_file_management_run(product_id, baseline_id, candidate_id)

    assert first.run.harness_run_id != second.run.harness_run_id
    assert first.run.changeset_id != second.run.changeset_id
    assert len(service.store.list("tool_policy", ToolPolicy, product_id)) == 2
    assert Service(str(tmp_path / "agentguard.db")).resume_file_management_run(first.run.harness_run_id).release_decision.status == "blocked"
    assert Service(str(tmp_path / "agentguard.db")).resume_file_management_run(second.run.harness_run_id).release_decision.status == "blocked"


def test_cli_and_api_expose_the_resilient_file_management_loop(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "agentguard.db")
    assert main(["--db", db, "--format", "json", "fixture", "load", "file-management-agent"]) == 0
    fixture = json.loads(capsys.readouterr().out)["data"]["fixture"]
    assert main([
        "--db", db, "--format", "json", "run", "start-file-management",
        "--product-id", fixture["product"]["product_id"],
        "--baseline", fixture["baseline"]["version_id"],
        "--candidate", fixture["candidate"]["version_id"],
    ]) == 0
    cli_result = json.loads(capsys.readouterr().out)["data"]
    assert cli_result["release_decision"]["status"] == "blocked"
    assert main(["--db", db, "--format", "json", "run", "resume", "--run-id", cli_result["harness_run"]["harness_run_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["operations"][0]["tool_call_count"] == 3

    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    client = TestClient(app)
    api_fixture = client.post("/api/v1/fixtures/file-management-agent").json()["fixture"]
    response = client.post("/api/v1/runs/file-management", json={
        "product_id": api_fixture["product"]["product_id"],
        "baseline_version_id": api_fixture["baseline"]["version_id"],
        "candidate_version_id": api_fixture["candidate"]["version_id"],
    })
    assert response.status_code == 200
    assert response.json()["release_decision"]["status"] == "blocked"
