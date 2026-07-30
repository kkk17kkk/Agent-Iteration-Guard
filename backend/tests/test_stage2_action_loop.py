import json

import pytest

from agentguard.domain import AgentAction, AgentObservation, ExecutionResult, Stage2AgentRun, Stage2Operation
from agentguard.service import Service
from agentguard.stage1 import Stage1AcceptanceGate
from agentguard.stage2 import Stage2InjectedCrash


def allow_stage2(service: Service, batch_id: str = "stage1-pass") -> None:
    service.store.save(
        "stage1_acceptance_gate",
        f"stage1_acceptance_gate__{batch_id}",
        "stage1",
        Stage1AcceptanceGate(batch_id=batch_id, status="PASS", criteria=[]),
    )


def test_stage2_completes_multistep_tasks_and_persists_trace(tmp_path):
    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)

    updated = service.start_stage2_file_agent("stage1-pass", task_kind="update_title")
    readonly = service.start_stage2_file_agent("stage1-pass", task_kind="read_only", model_kind="fake")
    blocked = service.start_stage2_file_agent("stage1-pass", task_kind="cleanup")

    assert updated.status == "finished"
    assert readonly.status == "finished"
    assert blocked.status == "blocked"
    assert service.report_stage2_file_agent(updated.agent_run_id, tmp_path / "artifacts")["action_kinds"] == [
        "read_file", "write_file", "read_file", "finish"
    ]
    assert service.report_stage2_file_agent(updated.agent_run_id, tmp_path / "artifacts")["observation_count"] == 5

    execution = service.store.list("execution", ExecutionResult, updated.product_id)
    assert any(item.harness_run_id == updated.harness_run_id and len(item.tool_calls) == 3 for item in execution)


def test_stage2_resume_after_side_effect_boundary_is_idempotent(tmp_path):
    db = str(tmp_path / "stage2.db")
    service = Service(db)
    allow_stage2(service)
    with pytest.raises(Stage2InjectedCrash):
        service.start_stage2_file_agent("stage1-pass", task_kind="update_title", crash_at="after_side_effect_before_commit")

    interrupted = service.store.list("stage2_agent_run", Stage2AgentRun)[-1]
    resumed = Service(db).resume_stage2_file_agent(interrupted.agent_run_id)
    operations = [
        item for item in Service(db).store.list("stage2_operation", Stage2Operation, resumed.product_id)
        if item.agent_run_id == resumed.agent_run_id
    ]
    assert resumed.status == "finished"
    assert resumed.duplicate_side_effect_count == 0
    assert len(operations) == 4
    assert all(item.status == "completed" for item in operations)


def test_stage2_rejects_stale_observation_and_prompt_injection(tmp_path):
    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)
    run = service.start_stage2_file_agent("stage1-pass", task_kind="update_title")
    observations = service.store.list("stage2_observation", AgentObservation, run.product_id)
    observation = next(item for item in observations if item.agent_run_id == run.agent_run_id and item.step == 1)
    stale = AgentAction(
        agent_run_id=run.agent_run_id,
        step=run.step_count + 1,
        kind="write_file",
        path="README.md",
        content="# unsafe\n",
        expected_observation_fingerprint="stale",
    )
    assert service.stage2.validate_action(run.agent_run_id, stale, observation) == "stale observation rejected"
    report = service.report_stage2_file_agent(run.agent_run_id, tmp_path / "artifacts")
    action_payload = json.loads((tmp_path / "artifacts" / "runs" / run.agent_run_id / "actions.json").read_text())
    assert all("secrets" not in json.dumps(item) for item in action_payload)
    assert report["status"] == "finished"


def test_stage2_gate_passes_only_after_acceptance_matrix(tmp_path):
    db = str(tmp_path / "stage2.db")
    service = Service(db)
    allow_stage2(service)
    with pytest.raises(Stage2InjectedCrash):
        service.start_stage2_file_agent("stage1-pass", task_kind="update_title", crash_at="after_side_effect_before_commit")
    interrupted = service.store.list("stage2_agent_run", Stage2AgentRun)[-1]
    service.resume_stage2_file_agent(interrupted.agent_run_id)
    service.start_stage2_file_agent("stage1-pass", task_kind="read_only", model_kind="fake")
    service.start_stage2_file_agent("stage1-pass", task_kind="cleanup")
    gate = service.gate_stage2_file_agent("stage1-pass", tmp_path / "artifacts")
    assert gate.status == "PASS"
    assert all(item.status == "verified" for item in gate.criteria)
