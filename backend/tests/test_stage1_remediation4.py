import json
import os
import subprocess
import sys

from agentguard.domain import ExecutionResult, HarnessRun, Operation
from agentguard.service import Service
from agentguard.stage1 import Stage1GroundTruth, Stage1ReplayAblationArtifact
from agentguard.stage1_reporting import _recompute_replay_metrics


def test_runner_result_uncommitted_is_recovered_across_processes(tmp_path):
    db = tmp_path / "uncommitted.db"
    code = """
from agentguard.resilient import InjectedCrash
from agentguard.service import Service
import os
service = Service(r'__DB__')
fixture = service.file_management_fixture()
try:
    service.start_file_management_run(
        fixture.product.product_id, fixture.baseline.version_id, fixture.candidate.version_id,
        crash_at='after_runner_before_execution_commit'
    )
except InjectedCrash:
    os._exit(23)
raise SystemExit(99)
""".replace("__DB__", str(db))
    completed = subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), check=False)
    assert completed.returncode == 23

    service = Service(str(db))
    runs = service.store.list("harness_run", HarnessRun)
    assert len(runs) == 1
    run = runs[0]
    operations = service.store.list("operation", Operation, run.product_id)
    assert len(operations) == 1
    assert operations[0].status == "interrupted"
    assert service.store.list("execution", ExecutionResult, run.product_id) == []

    resumed = service.resume_file_management_run(run.harness_run_id)
    assert resumed.run.status in {"recorded", "blocked"}
    assert len(service.store.list("operation", Operation, run.product_id)) == 1
    assert len(service.store.list("execution", ExecutionResult, run.product_id)) == 1


def test_replay_report_uses_ranked_root_causes_and_ground_truth_only_at_report(tmp_path):
    root = tmp_path / "artifacts"
    (root / "replay").mkdir(parents=True)
    artifacts = [
        Stage1ReplayAblationArtifact(
            artifact_id="replay-1",
            case_id="case-1",
            mutation_kind="permission",
            harness_run_id="run-1",
            source_trial_result_id="exec-1",
            replay_result_id="run-2",
            replay_reproduced=True,
            ablation_id="ablation-1",
            ablation_root_cause="runner_failure",
            candidate_root_causes=["runner_failure", "permission_violation", "oracle_mismatch"],
            ranked_root_causes=["runner_failure", "permission_violation", "oracle_mismatch"],
        )
    ]
    (root / "replay" / "stage1_replay_ablation.json").write_text(json.dumps([item.model_dump() for item in artifacts]), encoding="utf-8")
    metrics = _recompute_replay_metrics(root, {"case-1": Stage1GroundTruth(case_id="case-1", regression=True, severity="severe", required_case_ids=())})
    assert metrics is not None
    assert metrics.ablation_top1 == 0.0
    assert metrics.ablation_top3 == 1.0
    assert metrics.incorrect_attribution_rate == 0.0
