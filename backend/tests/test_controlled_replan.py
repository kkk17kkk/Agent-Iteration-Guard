import json

from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.domain import EvalCase, ExecutionResult, ReplayResult, ReplaySpec, RunnerFailure, ToolCall, TrialMetrics, TrialResult, VerificationResult
from agentguard.service import Service


def evaluated_run(service: Service):
    fixture = service.file_management_fixture()
    result = service.evaluate_file_management_trials(
        fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, [False, False, False]
    )
    return fixture, result.run


def test_incomplete_trace_adds_instrumentation_trial_and_auditable_record(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "replan.db")
    service = Service(db)
    fixture, run = evaluated_run(service)
    execution_id = service.store.list("trial_result", TrialResult, fixture.product.product_id)[0].execution_id
    execution = service.store.get("execution", execution_id, ExecutionResult)
    assert execution is not None
    # Avoid depending on the persisted implementation: overwrite this one durable trace as incomplete.
    execution = execution.model_copy(update={"output_fingerprint": None})
    service.store.save("execution", execution.execution_id, fixture.product.product_id, execution)

    assert main(["--db", db, "--format", "json", "run", "replan", "--run-id", run.harness_run_id]) == 0
    payload = json.loads(capsys.readouterr().out)["data"]
    record = payload["replan"]

    assert record["trigger"] == "incomplete_trace"
    assert record["terminal_reason"] == "applied"
    assert record["before_plan"]["eval_plan_id"] != record["after_plan"]["eval_plan_id"]
    assert record["added_work_item_ids"] == [payload["work_items"][0]["work_item_id"]]
    assert payload["trial_results"][0]["kind"] == "instrumentation"
    assert record["budget_before"] == record["budget_after"]

    second = ExecutionResult(
        harness_run_id=run.harness_run_id,
        work_item_id=payload["work_items"][0]["work_item_id"],
        tool_calls=[ToolCall(tool_name="read_file", path="README.md", policy_decision="allowed")],
    )
    service.store.save("execution", second.execution_id, fixture.product.product_id, second)
    monkeypatch.setenv("AGENTGUARD_DB", db)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/runs/{run.harness_run_id}/replan", json={})
    assert response.status_code == 200
    assert response.json()["replan"]["trigger"] == "incomplete_trace"


def test_unstable_results_consume_only_the_saved_trial_budget(tmp_path):
    service = Service(str(tmp_path / "replan.db"))
    fixture, run = evaluated_run(service)
    original = service.store.list("trial_metrics", TrialMetrics, fixture.product.product_id)[0]
    unstable = original.model_copy(update={"metrics_id": "metrics_unstable", "variance": 0.25})
    service.store.save("trial_metrics", unstable.metrics_id, fixture.product.product_id, unstable)

    first = service.controlled_replan_file_management(run.harness_run_id, additional_trial_budget=1)
    exhausted_source = unstable.model_copy(update={"metrics_id": "metrics_unstable_again"})
    service.store.save("trial_metrics", exhausted_source.metrics_id, fixture.product.product_id, exhausted_source)
    second = service.controlled_replan_file_management(run.harness_run_id, additional_trial_budget=1)

    assert first.record.trigger == "unstable_results"
    assert first.record.budget_after.additional_trial_used == 1
    assert first.trial_results[0].kind == "instrumentation"
    assert second.record.terminal_reason == "budget_exhausted"
    assert second.work_items == []


def test_permission_regression_adds_real_safety_eval_case_and_escalates_risk(tmp_path):
    service = Service(str(tmp_path / "replan.db"))
    fixture, run = evaluated_run(service)
    verification = VerificationResult(
        harness_run_id=run.harness_run_id,
        execution_id="execution_permission_source",
        expected="no delete",
        observed="delete attempted",
        passed=False,
        severity="critical",
        failure_class="tool_policy",
        failure_type="permission_violation",
    )
    service.store.save("verification", verification.verification_id, fixture.product.product_id, verification)

    result = service.controlled_replan_file_management(run.harness_run_id)

    assert result.record.trigger == "permission_regression"
    assert result.record.risk_escalated is True
    assert result.work_items[0].eval_case_id in {
        case.eval_case_id for case in service.store.list("eval_case", EvalCase, fixture.product.product_id)
    }
    assert result.trial_results[0].kind == "safety"


def test_runner_environment_failure_blocks_by_default_or_switches_only_when_explicit(tmp_path):
    service = Service(str(tmp_path / "replan.db"))
    fixture, run = evaluated_run(service)
    failure = RunnerFailure(harness_run_id=run.harness_run_id, runner="inspect_ai", category="environment", reason="sandbox image missing")
    service.store.save("runner_failure", failure.runner_failure_id, fixture.product.product_id, failure)

    blocked = service.controlled_replan_file_management(run.harness_run_id)

    assert blocked.record.terminal_reason == "runner_blocked"
    assert blocked.run.status == "blocked"

    fixture2, run2 = evaluated_run(Service(str(tmp_path / "switch.db")))
    switched_service = Service(str(tmp_path / "switch.db"))
    failure2 = RunnerFailure(harness_run_id=run2.harness_run_id, runner="inspect_ai", category="environment", reason="sandbox image missing")
    switched_service.store.save("runner_failure", failure2.runner_failure_id, fixture2.product.product_id, failure2)
    switched = switched_service.controlled_replan_file_management(run2.harness_run_id, allow_runner_switch=True)

    assert switched.record.terminal_reason == "applied"
    assert switched.record.risk_escalated is True
    assert switched.trial_results[0].kind == "instrumentation"


def test_unreproduced_replay_is_unresolved_and_captures_environment(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "replan.db")
    service = Service(db)
    fixture, run = evaluated_run(service)
    replay_spec = ReplaySpec(
        harness_run_id=run.harness_run_id, source_trial_result_id="source", candidate_fingerprint="candidate",
        policy_fingerprint="policy", environment_fingerprint="environment", cleanup_attempt=False, seed=1,
        source_trace_fingerprint="trace",
    )
    replay = ReplayResult(
        replay_spec_id=replay_spec.replay_spec_id, execution_id="execution", verification_id="verification",
        trace_fingerprint="different", reproduced=False,
    )
    service.store.save_many([
        ("replay_spec", replay_spec.replay_spec_id, fixture.product.product_id, replay_spec),
        ("replay_result", replay.replay_result_id, fixture.product.product_id, replay),
    ])

    result = service.controlled_replan_file_management(run.harness_run_id)

    assert result.record.trigger == "replay_not_reproduced"
    assert result.record.terminal_reason == "unresolved"
    assert result.run.status == "awaiting_evidence"
    assert result.environment_capture is not None
    assert result.work_items[0].expected_output_type == "environment_capture"

    assert main(["--db", db, "--format", "json", "run", "replan", "--run-id", run.harness_run_id]) == 3
    assert "No unhandled controlled Replan trigger" in json.loads(capsys.readouterr().out)["error"]["reason"]
    monkeypatch.setenv("AGENTGUARD_DB", db)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/runs/{run.harness_run_id}/replan", json={})
    assert response.status_code == 422
