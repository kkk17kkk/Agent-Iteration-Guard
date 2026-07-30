from agentguard.stage1 import (
    Stage1GroundTruth,
    Stage1Metrics,
    Stage1RawResult,
    build_stage1_runtime_corpus,
    build_stage1_corpus,
    compute_metrics,
    execute_case,
    persist_corpus_run,
    report_stage1_corpus,
    run_stage1_corpus,
    select_cases,
    write_artifacts,
)
import agentguard.stage1 as stage1_module
import os
import subprocess
import sys

from agentguard.domain import HarnessRun
from agentguard.store import Store
from agentguard.service import Service
from agentguard.domain import RunnerFailure
from agentguard.cli import main


def test_stage1_corpus_has_independent_hidden_combinations_and_recomputable_metrics():
    cases, truth = build_stage1_corpus()
    raw = [execute_case(case) for case in cases]
    metrics = compute_metrics(raw, truth)

    hidden = [case for case in cases if case.split == "hidden"]
    assert any(len(case.mutation_kinds) == 2 for case in hidden)
    assert {case.mutation_position for case in hidden} == {"planning", "execution", "verification"}
    assert any(not item.regression for item in truth if item.case_id.startswith("hidden-"))
    assert metrics.sample_count == len(cases)
    assert metrics.regression_recall == 1.0
    assert metrics.severe_regression_recall == 1.0
    assert metrics.false_block_rate == 0.0
    assert metrics.false_ready_rate == 0.0
    assert 0.0 < metrics.selection_recall < 1.0
    assert metrics.selection_reduction > 0
    assert metrics.severe_miss_case_ids == []


def test_router_does_not_receive_ground_truth_and_missed_required_case_is_measurable():
    cases, truth = build_stage1_corpus()
    case = next(item for item in cases if item.case_id == "hidden-skill-permission")
    assert "security" in select_cases(case)

    raw = [execute_case(item) for item in cases]
    damaged = [
        item.model_copy(
            update={
                "selected_case_ids": ("smoke",),
                "observed_failure_case_ids": (),
                "release_status": "ready",
            }
        )
        if item.case_id == case.case_id
        else item
        for item in raw
    ]
    metrics = compute_metrics(damaged, truth)
    assert metrics.severe_regression_recall < 1.0
    assert case.case_id in metrics.severe_miss_case_ids


def test_stage1_report_metrics_are_recomputed_from_saved_raw_records(tmp_path):
    store = Store(str(tmp_path / "stage1.db"))
    metrics = persist_corpus_run(store, "stage1-product")

    assert metrics.sample_count == 60
    assert store.get("stage1_metrics", "stage1_metrics", Stage1Metrics) == metrics
    assert len(store.list("stage1_raw_result", Stage1RawResult, "stage1-product")) == metrics.sample_count


def test_stage1_run_path_does_not_load_or_persist_ground_truth(tmp_path, monkeypatch):
    def forbidden_ground_truth(*args, **kwargs):
        raise AssertionError("Ground Truth was accessed by the run path")

    monkeypatch.setattr(stage1_module, "load_stage1_ground_truth", forbidden_ground_truth)
    store = Store(str(tmp_path / "runtime-only.db"))
    raw = run_stage1_corpus(store, "runtime-only")

    assert len(raw) == 60
    assert store.list("stage1_ground_truth", Stage1GroundTruth, "runtime-only") == []
    assert store.list("stage1_mutation", stage1_module.Stage1MutationManifest, "runtime-only")


def test_stage1_ground_truth_is_only_joined_during_reporting(tmp_path):
    store = Store(str(tmp_path / "report.db"))
    run_stage1_corpus(store, "report-only")
    assert store.list("stage1_ground_truth", Stage1GroundTruth, "report-only") == []

    metrics = report_stage1_corpus(store, "report-only")
    assert metrics.sample_count == 60


def test_stage1_runtime_case_has_no_ground_truth_or_failure_label_fields():
    cases, mutations = build_stage1_runtime_corpus()

    assert not hasattr(cases[0], "required_case_ids")
    assert not hasattr(cases[0], "actual_failure_case_ids")
    assert {mutation.case_id for mutation in mutations} == {case.case_id for case in cases}


def test_stage1_benchmark_has_a_cli_reproduction_entrypoint(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "stage1.db"), "--format", "json", "benchmark", "stage1"]) == 0
    output = __import__("json").loads(capsys.readouterr().out)["data"]
    assert output["sample_count"] == 60
    assert output["severe_regression_recall"] == 1.0


def test_stage1_writes_recomputable_artifacts(tmp_path):
    metrics = write_artifacts(Store(str(tmp_path / "stage1.db")), "stage1-product", tmp_path / "artifacts")
    assert metrics.sample_count == 60
    assert (tmp_path / "artifacts" / "raw_results" / "stage1_raw_results.json").is_file()
    assert (tmp_path / "artifacts" / "metrics" / "stage1_metrics.json").is_file()
    assert (tmp_path / "artifacts" / "reproduction_commands.md").is_file()


def test_stage1_cross_process_termination_resumes_without_duplicate_operation(tmp_path):
    db = tmp_path / "terminated.db"
    code = """
from agentguard.resilient import InjectedCrash
from agentguard.service import Service
import os
service = Service(r'__DB__')
fixture = service.file_management_fixture()
try:
    service.start_file_management_run(
        fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id, crash_at='after_runner'
    )
except InjectedCrash:
    os._exit(23)
raise SystemExit(99)
""".replace("__DB__", str(db))
    completed = subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), check=False)
    assert completed.returncode == 23

    service = Service(str(db))
    run = service.store.list("harness_run", HarnessRun)[0]
    resumed = service.resume_file_management_run(run.harness_run_id)
    assert resumed.release_decision.status == "blocked"
    assert len(resumed.operations) == 1
    assert resumed.operations[0].tool_call_count == 3


def test_stage1_release_can_be_recomputed_from_persisted_evidence_only(tmp_path):
    service = Service(str(tmp_path / "evidence.db"))
    fixture = service.file_management_fixture()
    original = service.start_file_management_run(
        fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id
    ).release_decision

    recomputed = Service(str(tmp_path / "evidence.db")).recompute_release_decision(original.harness_run_id)
    assert recomputed.status == original.status == "blocked"
    assert recomputed.finding_ids == original.finding_ids


def test_stage1_budget_exhaustion_is_visible_and_not_an_agent_regression(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    service = Service(str(tmp_path / "budget.db"))
    fixture = service.file_management_fixture()
    result = service.evaluate_file_management_external_trials(
        fixture.product.product_id,
        fixture.baseline.version_id,
        fixture.candidate.version_id,
        max_total_cost_usd=0.00000001,
    )

    assert result.run.status == "failed"
    assert result.release_decision.status == "pending"
    failures = service.store.list("runner_failure", RunnerFailure, fixture.product.product_id)
    assert failures[0].category == "budget"


def test_stage1_malformed_runner_output_is_visible_and_unresolved(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("agentguard.inspect_runner.get_model", lambda *args, **kwargs: object())

    class Output:
        completion = "not-json"

    class Sample:
        output = Output()
        model_usage = {"openai/deepseek-v4-flash": type("Usage", (), {"input_tokens": 1, "output_tokens": 1, "input_tokens_cache_write": 0, "input_tokens_cache_read": 0})()}

    class Log:
        samples = [Sample()]
        location = "D:/codexdata/malformed.eval"

    monkeypatch.setattr("agentguard.inspect_runner.inspect_eval", lambda *args, **kwargs: [Log()])
    service = Service(str(tmp_path / "malformed.db"))
    fixture = service.file_management_fixture()
    result = service.evaluate_file_management_external_trials(fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id)
    assert result.run.status == "failed"
    assert result.release_decision.status == "pending"
    assert service.store.list("runner_failure", RunnerFailure, fixture.product.product_id)[0].category == "contract"
