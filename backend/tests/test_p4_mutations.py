import pytest
from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.domain import BatchItem, ComponentSnapshot, MutationPair, Operation, ProviderUsage, ReleaseDecision, RunnerFailure, RunnerTrace, TrialResult
from agentguard.service import Service


def test_mutation_factory_creates_sixty_valid_programmatic_pairs(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture, pairs = service.generate_file_management_mutation_pairs()

    assert len(pairs) == 60
    assert {pair.mutation_kind for pair in pairs} == {
        "prompt", "skill", "tool_schema", "permission", "workflow"
    }
    assert sum(pair.expected_release == "blocked" for pair in pairs) == 30
    assert all(pair.valid and pair.rejection_reason is None for pair in pairs)
    assert all(pair.baseline_version_id == fixture.baseline.version_id for pair in pairs)

    stored = service.store.list("mutation_pair", MutationPair, fixture.product.product_id)
    snapshots = service.store.list("snapshot", ComponentSnapshot, fixture.product.product_id)
    assert {pair.pair_id for pair in stored} == {pair.pair_id for pair in pairs}
    assert len(snapshots) == 62

    for pair in pairs:
        changeset = service.compare_versions(
            fixture.product.product_id, pair.baseline_version_id, pair.candidate_version_id
        )
        assert changeset.changes


def test_batch_recovers_after_durable_boundary_and_blocks_permission_pairs(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    created = service.create_file_management_mutation_batch(max_workers=2)

    with pytest.raises(RuntimeError, match="Injected batch crash"):
        service.run_file_management_mutation_batch(created.batch.batch_id, crash_after_completed=1)

    interrupted = service.store.get("batch_run", created.batch.batch_id, type(created.batch))
    assert interrupted.status == "interrupted"
    resumed = Service(str(tmp_path / "agentguard.db")).run_file_management_mutation_batch(created.batch.batch_id)
    assert resumed.batch.status == "completed"
    assert len(resumed.items) == 60
    assert all(item.status in {"completed", "cached"} for item in resumed.items)
    assert max(checkpoint.next_pair_index for checkpoint in resumed.checkpoints) == 60

    pair_by_id = {pair.pair_id: pair for pair in resumed.pairs}
    decisions = {
        item.harness_run_id: service.store.list("release_decision", ReleaseDecision, resumed.batch.product_id)
        for item in resumed.items
    }
    observed = {
        pair_by_id[item.pair_id].expected_release:
        next(decision.status for decision in decisions[item.harness_run_id] if decision.harness_run_id == item.harness_run_id)
        for item in resumed.items
    }
    assert observed["blocked"] == "blocked"
    assert observed["ready"] == "ready"

    failed_item = next(item for item in resumed.items if pair_by_id[item.pair_id].expected_release == "blocked")
    failed_trial = next(
        trial for trial in service.store.list("trial_result", TrialResult, resumed.batch.product_id)
        if trial.harness_run_id == failed_item.harness_run_id and not trial.passed and trial.kind == "evaluation"
    )
    replayed = service.replay_file_management_trial(failed_item.harness_run_id, failed_trial.trial_result_id)
    ablated = service.ablate_file_management_cleanup(failed_item.harness_run_id, failed_trial.trial_result_id)
    assert replayed.replays[0].reproduced is True
    assert ablated.ablations[0].after_value is False

    rerun = service.create_file_management_mutation_batch(product_id=resumed.batch.product_id)
    cached = service.run_file_management_mutation_batch(rerun.batch.batch_id)
    assert all(item.status == "cached" for item in cached.items)


def test_cli_and_api_create_batch_contracts(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "cli.db")
    assert main(["--db", db, "--format", "json", "benchmark", "create-file-management"]) == 0
    assert len(__import__("json").loads(capsys.readouterr().out)["data"]["items"]) == 60

    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    response = TestClient(app).post("/api/v1/benchmarks/file-management", json={"max_workers": 2, "trials_per_pair": 3})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 60


def test_multi_trial_run_resumes_from_a_durable_trial_boundary(tmp_path):
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_management_fixture()
    run_id = "harness_partial_trial"
    with pytest.raises(RuntimeError, match="durable trial boundary"):
        service.evaluate_file_management_trials(
            fixture.product.product_id,
            fixture.baseline.version_id,
            fixture.candidate.version_id,
            [False, False, True],
            harness_run_id=run_id,
            crash_after_trial_count=1,
        )

    resumed = Service(str(tmp_path / "agentguard.db")).evaluate_file_management_trials(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
        [False, False, True],
        harness_run_id=run_id,
    )
    assert [result.passed for result in resumed.results] == [True, True, False]
    operations = [
        operation for operation in service.store.list("operation", Operation, fixture.product.product_id)
        if operation.harness_run_id == run_id
    ]
    assert len(operations) == 3


def _inspect_log(cleanup_attempt: bool):
    class Usage:
        input_tokens = 100
        output_tokens = 10
        input_tokens_cache_write = 0
        input_tokens_cache_read = 0
        total_cost = 0.0000168

    class Output:
        completion = '{"cleanup_attempt": ' + str(cleanup_attempt).lower() + "}"

    class Sample:
        output = Output()
        model_usage = {"openai/deepseek-v4-flash": Usage()}

    class Log:
        samples = [Sample()]
        location = "D:/codexdata/agentguard-inspect-logs/fake.eval"

    return [Log()]


def test_external_inspect_runner_persists_usage_and_oracle_remains_authoritative(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    captured: dict[str, object] = {}

    def fake_get_model(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("agentguard.inspect_runner.get_model", fake_get_model)
    monkeypatch.setattr("agentguard.inspect_runner.inspect_eval", lambda *args, **kwargs: _inspect_log(True))
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_management_fixture()

    result = service.evaluate_file_management_external_trials(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
    )

    assert result.release_decision.status == "blocked"
    assert len(result.results) == 3
    assert all(not trial.passed for trial in result.results)
    assert len(result.provider_usage) == 3
    assert len(result.runner_traces) == 3
    assert all(usage.total_cost_usd == pytest.approx(0.0000168) for usage in result.provider_usage)
    assert result.metrics.total_cost_usd == pytest.approx(0.0000504)
    assert all(trace.selected_cleanup_attempt is True for trace in result.runner_traces)
    assert captured["responses_api"] is False


def test_external_inspect_contract_failure_stays_unresolved(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("agentguard.inspect_runner.get_model", lambda *args, **kwargs: object())

    class BadOutput:
        completion = "{}"

    class Sample:
        output = BadOutput()
        model_usage = {"openai/deepseek-v4-flash": type("Usage", (), {
            "input_tokens": 100, "output_tokens": 10, "input_tokens_cache_write": 0,
            "input_tokens_cache_read": 0, "total_cost": 0.0000168,
        })()}

    class Log:
        samples = [Sample()]
        location = "D:/codexdata/agentguard-inspect-logs/fake.eval"

    monkeypatch.setattr("agentguard.inspect_runner.inspect_eval", lambda *args, **kwargs: [Log()])
    service = Service(str(tmp_path / "agentguard.db"))
    fixture = service.file_management_fixture()
    result = service.evaluate_file_management_external_trials(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
    )

    assert result.release_decision.status == "pending"
    assert result.run.status == "failed"
    assert result.runner_failures[0].category == "contract"
    assert len(result.provider_usage) == 1
