import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from agentguard.domain import AgentAction, AgentObservation, ExecutionResult, Stage2AgentRun, Stage2Operation
from agentguard.llm import Completion
from agentguard.service import Service
from agentguard.stage1 import Stage1AcceptanceGate
from agentguard.stage2 import JsonActionModel, Stage2InjectedCrash
from agentguard.api import app
from fastapi.testclient import TestClient


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
    appended = service.start_stage2_file_agent("stage1-pass", task_kind="append_note")
    blocked = service.start_stage2_file_agent("stage1-pass", task_kind="cleanup")
    approved = service.start_stage2_file_agent("stage1-pass", task_kind="cleanup_allowed")

    assert updated.status == "finished"
    assert readonly.status == "finished"
    assert appended.status == "finished"
    assert blocked.status == "blocked"
    assert approved.status == "finished"
    assert service.report_stage2_file_agent(updated.agent_run_id, tmp_path / "artifacts")["action_kinds"] == [
        "read_file", "write_file", "read_file", "finish"
    ]
    assert service.report_stage2_file_agent(updated.agent_run_id, tmp_path / "artifacts")["observation_count"] == 5

    execution = service.store.list("execution", ExecutionResult, updated.product_id)
    assert any(item.harness_run_id == updated.harness_run_id and len(item.tool_calls) == 3 for item in execution)
    recomputed = service.recompute_release_decision(updated.harness_run_id)
    assert recomputed.status == "ready"
    assert recomputed.finding_ids == []
    blocked_recomputed = service.recompute_release_decision(blocked.harness_run_id)
    assert blocked_recomputed.status == "blocked"


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
    service.start_stage2_file_agent("stage1-pass", task_kind="append_note")
    service.start_stage2_file_agent("stage1-pass", task_kind="cleanup")
    service.start_stage2_file_agent("stage1-pass", task_kind="cleanup_allowed")
    service.start_stage2_file_agent("stage1-pass", task_kind="nearby_file")
    gate = service.gate_stage2_file_agent("stage1-pass", tmp_path / "artifacts")
    assert gate.status == "BLOCKED"
    assert gate.deterministic_harness_status == "PASS"
    assert gate.real_llm_integration_status == "missing"
    assert all(item.status == "verified" for item in gate.criteria if item.criterion != "real_llm_agent_integration")
    real_criterion = next(item for item in gate.criteria if item.criterion == "real_llm_agent_integration")
    assert real_criterion.status == "missing"
    assert (tmp_path / "artifacts" / "reports" / "stage2_acceptance_report.json").is_file()
    assert list((tmp_path / "artifacts" / "runs").iterdir())


def test_stage2_resume_uses_real_child_process_boundaries(tmp_path):
    db = str(tmp_path / "stage2.db")
    service = Service(db)
    allow_stage2(service)
    crash_script = """
from agentguard.service import Service
from agentguard.stage2 import Stage2InjectedCrash
try:
    Service(r'{db}').start_stage2_file_agent('stage1-pass', task_kind='update_title', crash_at='after_side_effect_before_commit')
except Stage2InjectedCrash:
    raise SystemExit(23)
raise SystemExit(0)
""".format(db=db.replace("\\", "\\\\"))
    backend_root = str(__file__.replace("\\tests\\test_stage2_action_loop.py", ""))
    crashed = subprocess.run([sys.executable, "-c", crash_script], cwd=backend_root, capture_output=True, text=True)
    assert crashed.returncode == 23, crashed.stderr
    interrupted = Service(db).store.list("stage2_agent_run", Stage2AgentRun)[-1]
    resume_script = """
from agentguard.service import Service
run = Service(r'{db}').resume_stage2_file_agent('{run_id}')
assert run.status == 'finished'
assert run.duplicate_side_effect_count == 0
""".format(db=db.replace("\\", "\\\\"), run_id=interrupted.agent_run_id)
    resumed = subprocess.run([sys.executable, "-c", resume_script], cwd=backend_root, capture_output=True, text=True)
    assert resumed.returncode == 0, resumed.stderr


def test_external_json_model_uses_the_same_typed_action_protocol():
    class StubAssistant:
        def complete_json(self, system_prompt, input_payload):
            return Completion(provider_request_id="stub-1", model="stub", content='{"kind":"read_file","path":"README.md"}')

    run = Stage2AgentRun(
        product_id="product",
        harness_run_id="harness",
        work_item_id="work",
        stage1_batch_id="stage1-pass",
        task_kind="read_only",
        policy_id="policy",
        sandbox_path="D:/codexdata/stub",
        step_count=0,
        tool_manifest={"read_file": {"required": ["path"]}},
    )
    observation = AgentObservation(agent_run_id=run.agent_run_id, step=0, state_fingerprint="state", files={"README.md": "# Original\n"})
    action = JsonActionModel(StubAssistant()).propose(run, observation)
    assert isinstance(action, AgentAction)
    assert action.kind == "read_file"
    assert action.expected_observation_fingerprint == "state"


def test_external_agent_cannot_supply_oracle_label():
    class LabelingAssistant:
        def complete_json(self, system_prompt, input_payload):
            return Completion(provider_request_id="stub-label", model="stub", content='{"kind":"finish","failure":true}')

    run = Stage2AgentRun(
        product_id="product",
        harness_run_id="harness",
        work_item_id="work",
        stage1_batch_id="stage1-pass",
        task_kind="read_only",
        policy_id="policy",
        sandbox_path="D:/codexdata/stub",
    )
    observation = AgentObservation(agent_run_id=run.agent_run_id, step=0, state_fingerprint="state")
    with pytest.raises(ValueError, match="invalid AgentAction"):
        JsonActionModel(LabelingAssistant()).propose(run, observation)


def test_registered_json_model_can_drive_a_real_tool_run(tmp_path):
    class ScriptedAssistant:
        def complete_json(self, system_prompt, input_payload):
            if input_payload["task"].get("last_kind") == "read_file":
                content = '{"kind":"finish"}'
            else:
                content = '{"kind":"read_file","path":"README.md"}'
            return Completion(provider_request_id="stub-run", model="stub", content=content)

    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)
    run = service.start_stage2_file_agent(
        "stage1-pass",
        task_kind="read_only",
        model_kind="json",
        action_model=JsonActionModel(ScriptedAssistant()),
    )
    assert run.status == "finished"
    assert service.report_stage2_file_agent(run.agent_run_id, tmp_path / "artifacts")["action_kinds"] == ["read_file", "finish"]


def test_http_api_drives_real_external_agent_process_into_harness(tmp_path, monkeypatch):
    class AgentHandler(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):  # noqa: N802 - stdlib handler API
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            AgentHandler.calls += 1
            task = payload["task"]
            planned = task.get("planned_kinds", [])
            if not planned:
                action = {"kind": "read_file", "path": "README.md"}
            elif planned[-1] == "read_file" and len(planned) == 1:
                action = {"kind": "write_file", "path": "README.md", "content": "# XXX\nManaged by the fixture.\n"}
            elif planned[-1] == "write_file":
                action = {"kind": "read_file", "path": "README.md"}
            else:
                action = {"kind": "finish"}
            body = json.dumps(action).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 - stdlib handler API
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), AgentHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db = str(tmp_path / "api-stage2.db")
    allow_stage2(Service(db))
    monkeypatch.setenv("AGENTGUARD_DB", db)
    monkeypatch.setenv("AGENTGUARD_STAGE2_MODEL_URL", f"http://127.0.0.1:{server.server_port}/action")
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/stage2/runs", json={"stage1_batch_id": "stage1-pass", "task_kind": "update_title", "model_kind": "http_json"})
            assert response.status_code == 200, response.text
            payload = response.json()
            run_id = payload["agent_run_id"]
            assert payload["status"] == "finished"
            assert payload["model_kind"] == "http_json"
            report = client.get(f"/api/v1/stage2/runs/{run_id}/report")
            assert report.status_code == 200, report.text
            assert report.json()["action_kinds"] == ["read_file", "write_file", "read_file", "finish"]
        assert AgentHandler.calls == 4
    finally:
        server.shutdown()
        server.server_close()
