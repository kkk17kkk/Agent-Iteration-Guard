import json

from agentguard.cli import main
from agentguard.domain import HarnessRun
from agentguard.service import Service
from agentguard.stage1_reporting import (
    build_stage1_artifacts,
    gate_stage1_report,
    report_stage1_artifacts,
)


CASE_IDS = ["dev-workflow-normal", "dev-skill-normal", "dev-permission-severe"]


def test_remediation3_build_run_report_gate_layout_and_raw_recompute(tmp_path):
    root = tmp_path / "artifacts" / "stage_1"
    build = build_stage1_artifacts(root)
    assert build.case_count == 60
    assert (root / "corpus" / "ground_truth" / "ground_truth_manifest.jsonl").is_file()

    service = Service(str(tmp_path / "stage1.db"))
    batch = service.run_stage1_harness_corpus(
        CASE_IDS,
        corpus_root=root / "corpus",
        artifacts_root=root,
    )
    report = report_stage1_artifacts(service.store, batch.batch_id, root)
    assert report.sample_count == 3
    assert report.mutation_type_confusion
    assert report.combination_type_confusion
    assert (root / "runs" / "selected" / "dev-permission-severe.json").is_file()

    raw_path = root / "raw_results" / "stage1_harness_artifacts.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    selected = next(item for item in raw if item["case_id"] == "dev-workflow-normal" and item["branch"] == "selected")
    selected["release_status"] = "blocked"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    recomputed = report_stage1_artifacts(service.store, batch.batch_id, root)
    assert recomputed.false_block_case_ids == ["dev-workflow-normal"]

    gate = gate_stage1_report(service.store, batch.batch_id, root)
    assert gate.status == "BLOCKED"
    assert any(item.criterion == "reliability_metrics" and item.status == "missing" for item in gate.criteria)


def test_remediation3_cli_build_has_no_evaluation_side_effect(tmp_path, capsys):
    db = str(tmp_path / "stage1.db")
    root = tmp_path / "artifacts"
    assert main(["--db", db, "--format", "json", "benchmark", "stage1", "build", "--artifacts-root", str(root)]) == 0
    output = json.loads(capsys.readouterr().out)["data"]
    assert output["case_count"] == 60
    assert not Service(db).store.list("harness_run", HarnessRun)
