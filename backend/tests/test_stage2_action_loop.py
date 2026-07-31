import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from agentguard.domain import AgentAction, AgentObservation, ExecutionResult, Stage2AgentRun, Stage2ModelCall, Stage2Operation
from agentguard.llm import Completion
from agentguard.service import Service
from agentguard.stage1 import Stage1AcceptanceGate
from agentguard.stage2 import JsonActionModel, Stage2InjectedCrash
from agentguard.api import app
from agentguard.cli import main
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
    assert all(item.status == "verified" for item in gate.criteria if not item.criterion.startswith("real_llm_"))
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
    action = JsonActionModel(StubAssistant()).propose(run, observation).action
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


def test_invalid_external_action_is_persisted_as_model_failure_not_agent_regression(tmp_path):
    class InvalidAssistant:
        provider = "external"

        def complete_json(self, system_prompt, input_payload):
            return Completion(provider_request_id="invalid-1", model="invalid", content='{"kind":"finish","failure":true}')

    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)
    run = service.start_stage2_file_agent(
        "stage1-pass", task_kind="read_only", model_kind="json", action_model=JsonActionModel(InvalidAssistant())
    )
    calls = [item for item in service.store.list("stage2_model_call", Stage2ModelCall, run.product_id) if item.agent_run_id == run.agent_run_id]

    assert run.status == "failed"
    assert run.terminal_reason == "model_invalid_response"
    assert len(calls) == 1
    assert calls[0].outcome == "invalid_response"
    assert calls[0].provider_request_id == "invalid-1"


def test_registered_json_model_can_drive_a_real_tool_run(tmp_path):
    class ScriptedAssistant:
        def complete_json(self, system_prompt, input_payload):
            if input_payload["observation"].get("last_action_kind") == "read_file":
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
            observation = payload["observation"]
            previous = observation.get("last_action_kind")
            if previous is None:
                action = {"kind": "read_file", "path": "README.md"}
            elif previous == "read_file":
                if observation["files"].get("README.md") == "# XXX\nManaged by the fixture.\n":
                    action = {"kind": "finish"}
                else:
                    action = {"kind": "write_file", "path": "README.md", "content": "# XXX\nManaged by the fixture.\n"}
            elif previous == "write_file":
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


def test_ensure_title_pair_is_observation_driven_and_oracle_checked(tmp_path):
    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)
    needs_update = service.start_stage2_file_agent(
        "stage1-pass", task_kind="ensure_title", fixture_variant="needs_update"
    )
    already_satisfied = service.start_stage2_file_agent(
        "stage1-pass", task_kind="ensure_title", fixture_variant="already_satisfied"
    )
    needs_report = service.report_stage2_file_agent(needs_update.agent_run_id, tmp_path / "artifacts")
    satisfied_report = service.report_stage2_file_agent(already_satisfied.agent_run_id, tmp_path / "artifacts")

    assert needs_update.status == "finished"
    assert already_satisfied.status == "finished"
    assert needs_report["action_kinds"] == ["read_file", "write_file", "read_file", "finish"]
    assert satisfied_report["action_kinds"] == ["read_file", "finish"]
    assert needs_report["observation_fingerprints"][1] != satisfied_report["observation_fingerprints"][1]
    assert needs_report["model_calls"]


def test_native_tool_runtime_batch_persists_usage_trace_and_budget_gate(tmp_path, monkeypatch):
    responses = iter([
        ("read_file", {"path": "README.md"}),
        ("write_file", {"path": "README.md", "content": "# XXX\nManaged by the fixture.\n"}),
        ("read_file", {"path": "README.md"}),
        ("finish_task", {}),
        ("read_file", {"path": "temporary.txt"}),
        ("delete_file", {"path": "temporary.txt"}),
    ])

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        name, arguments = next(responses)
        return Response({
            "id": f"provider-{name}",
            "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
                "id": f"tool-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)},
            }]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    monkeypatch.setattr("agentguard.stage2.urlopen", fake_urlopen)
    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)
    batch, runs, gate = service.run_stage2_native_runtime_batch("stage1-pass", budget_limit_usd=0.01, max_steps_per_run=6)

    assert [run.status for run in runs] == ["finished", "blocked"]
    assert gate.status == "PASS"
    assert gate.observed_cost_usd > 0
    assert len(gate.provider_usage_ids) == 6
    assert len(gate.native_trace_call_ids) == 6
    assert service.stage2.gate_runtime_batch(batch.runtime_batch_id).status == "PASS"
    report = service.stage2.report_runtime_batch(batch.runtime_batch_id, tmp_path / "artifacts")
    assert report["budget_gate"]["status"] == "PASS"
    assert (tmp_path / "artifacts" / "runtime_batches" / batch.runtime_batch_id / "runtime_batch_report.json").is_file()


def test_native_tool_runtime_refuses_request_before_batch_budget_is_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("agentguard.stage2.urlopen", lambda *args, **kwargs: pytest.fail("budget gate must run before provider request"))
    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)
    batch, runs, gate = service.run_stage2_native_runtime_batch("stage1-pass", budget_limit_usd=1e-8, max_steps_per_run=6)

    assert all(run.status == "budget_exhausted" for run in runs)
    assert gate.status == "BLOCKED"
    assert gate.observed_cost_usd == 0
    assert service.stage2.gate_runtime_batch(batch.runtime_batch_id).status == "BLOCKED"


def test_retry_idempotency_runtime_corpus_replays_and_ablates_actual_side_effects(tmp_path):
    service = Service(str(tmp_path / "stage2.db"))
    allow_stage2(service)

    corpus, gate = service.run_stage2_retry_idempotency_corpus("stage1-pass")
    report = service.stage2.report_retry_idempotency_corpus(corpus.corpus_id, tmp_path / "artifacts")

    assert corpus.mutation_kind == "retry_idempotency"
    assert corpus.trial_count == 3
    assert gate.status == "PASS_WITH_LIMITATIONS"
    assert all(item["verified"] for item in gate.criteria)
    trials = report["trials"]
    stable = [item for item in trials if item["retry_mode"] == "stable_operation_id"]
    mutant = [item for item in trials if item["retry_mode"] == "regenerate_operation_id"]
    assert len(stable) == len(mutant) == 3
    assert all(item["duplicate_side_effect_count"] == 0 and item["release_status"] == "ready" for item in stable)
    assert all(item["duplicate_side_effect_count"] == 1 and item["release_status"] == "blocked" for item in mutant)
    assert report["replay"]["trace_matches"] is True
    assert report["replay"]["duplicate_side_effect_count"] == 1
    assert report["ablation"]["trace_matches"] is True
    assert report["ablation"]["duplicate_side_effect_count"] == 0
    assert (tmp_path / "artifacts" / "reliability_corpora" / corpus.corpus_id / "retry_idempotency_report.json").is_file()


def test_retry_idempotency_corpus_cli_has_json_success_and_visible_invalid_trial_failure(tmp_path, capsys):
    db = str(tmp_path / "stage2-cli.db")
    allow_stage2(Service(db))

    assert main([
        "--db", db, "--format", "json", "stage2", "reliability-corpus",
        "--batch-id", "stage1-pass", "--model", "deterministic", "--trials", "3",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ]) == 0
    success = json.loads(capsys.readouterr().out)
    assert success["data"]["gate"]["status"] == "PASS_WITH_LIMITATIONS"

    assert main([
        "--db", db, "--format", "json", "stage2", "reliability-corpus",
        "--batch-id", "stage1-pass", "--model", "deterministic", "--trials", "2",
    ]) == 3
    failure = json.loads(capsys.readouterr().out)
    assert "at least three trials" in failure["error"]["reason"]
