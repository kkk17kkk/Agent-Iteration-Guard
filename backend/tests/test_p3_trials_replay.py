import json

import pytest
from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.domain import ReplayResult, TrialResult, VerificationResult
from agentguard.service import AssistantInputError, Service


def test_three_trials_compute_reproducible_stability_metrics_and_block_release(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_management_fixture()

    result = service.evaluate_file_management_trials(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
        [False, False, True],
    )

    assert [item.passed for item in result.results] == [True, True, False]
    assert result.metrics.trial_count == 3
    assert result.metrics.success_rate == pytest.approx(2 / 3)
    assert result.metrics.variance == pytest.approx(2 / 9)
    assert result.metrics.mean_latency_ms >= 0
    assert result.metrics.total_cost_usd == 0.0
    assert result.release_decision.status == "blocked"
    assert all(item.cost_usd == 0.0 for item in result.results)
    assert all(item.trace_fingerprint for item in result.results)

    persisted = service.store.list("trial_result", TrialResult, fixture.product.product_id)
    assert {item.trial_result_id for item in persisted if item.kind == "evaluation"} == {
        item.trial_result_id for item in result.results
    }


def test_fixed_environment_replay_and_single_variable_cleanup_ablation(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_management_fixture()
    result = service.evaluate_file_management_trials(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
        [False, False, True],
    )
    failed = next(item for item in result.results if not item.passed)

    replayed = service.replay_file_management_trial(result.run.harness_run_id, failed.trial_result_id)
    ablated = service.ablate_file_management_cleanup(result.run.harness_run_id, failed.trial_result_id)

    assert replayed.replays[0].reproduced is True
    replay = service.store.get("replay_result", replayed.replays[0].replay_result_id, ReplayResult)
    assert replay.trace_fingerprint == failed.trace_fingerprint
    report = ablated.ablations[0]
    assert report.before_value is True
    assert report.after_value is False
    after = service.store.get("verification", report.after_verification_id, VerificationResult)
    assert after.passed is True
    assert ablated.metrics.trial_count == 3
    assert ablated.metrics.success_rate == pytest.approx(2 / 3)


def test_replay_rejects_a_changed_policy_context(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_management_fixture()
    result = service.evaluate_file_management_trials(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
        [False, False, True],
    )
    failed = next(item for item in result.results if not item.passed)
    policy = service._policy_for_run(result.run)
    changed_policy = policy.model_copy(update={"allow_delete": True})
    service.store.save("tool_policy", policy.policy_id, fixture.product.product_id, changed_policy)

    with pytest.raises(AssistantInputError, match="Tool policy changed"):
        service.replay_file_management_trial(result.run.harness_run_id, failed.trial_result_id)


def test_cli_and_api_expose_trial_metrics_replay_and_ablation(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "agentguard.db")
    assert main(["--db", db, "--format", "json", "fixture", "load", "file-management-agent"]) == 0
    fixture = json.loads(capsys.readouterr().out)["data"]["fixture"]
    assert main([
        "--db", db, "--format", "json", "run", "evaluate",
        "--product-id", fixture["product"]["product_id"],
        "--baseline", fixture["baseline"]["version_id"],
        "--candidate", fixture["candidate"]["version_id"],
    ]) == 0
    evaluated = json.loads(capsys.readouterr().out)["data"]
    assert evaluated["metrics"]["success_rate"] == pytest.approx(2 / 3)
    failed = next(item for item in evaluated["trial_results"] if not item["passed"])
    assert main([
        "--db", db, "--format", "json", "run", "replay",
        "--run-id", evaluated["harness_run"]["harness_run_id"],
        "--source-trial-result-id", failed["trial_result_id"],
    ]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["replays"][0]["reproduced"] is True

    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    client = TestClient(app)
    api_fixture = client.post("/api/v1/fixtures/file-management-agent").json()["fixture"]
    response = client.post("/api/v1/runs/trials", json={
        "product_id": api_fixture["product"]["product_id"],
        "baseline_version_id": api_fixture["baseline"]["version_id"],
        "candidate_version_id": api_fixture["candidate"]["version_id"],
        "cleanup_attempts": [False, False, True],
    })
    assert response.status_code == 200
    assert response.json()["metrics"]["trial_count"] == 3
