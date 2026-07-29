from agentguard.stage1 import (
    Stage1Metrics,
    Stage1RawResult,
    build_stage1_corpus,
    compute_metrics,
    execute_case,
    persist_corpus_run,
    select_cases,
)
import os
import subprocess
import sys

from agentguard.domain import HarnessRun
from agentguard.store import Store
from agentguard.service import Service
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
    assert metrics.selection_recall == 1.0
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

    assert metrics.sample_count == 8
    assert store.get("stage1_metrics", "stage1_metrics", Stage1Metrics) == metrics
    assert len(store.list("stage1_raw_result", Stage1RawResult, "stage1-product")) == metrics.sample_count


def test_stage1_benchmark_has_a_cli_reproduction_entrypoint(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "stage1.db"), "--format", "json", "benchmark", "stage1"]) == 0
    output = __import__("json").loads(capsys.readouterr().out)["data"]
    assert output["sample_count"] == 8
    assert output["severe_regression_recall"] == 1.0


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
