import io
import importlib
import json
import sys
import types
from pathlib import Path

from agentguard.integrations.paperagent_case import PaperAgentEvidence, PaperAgentInvalidUrlVerifier
from agentguard.integrations.paperagent_profile import PAPERAGENT_CLIENT_SCRIPT, PAPERAGENT_PROJECT_PROFILE


def evidence(**updates: object) -> PaperAgentEvidence:
    values: dict[str, object] = {
        "event_completed": True,
        "response": {"diagnostic_status": "输入的内容不是有效的论文链接，请粘贴 https://example.org/paper.pdf"},
        "initial_files": {"paper_agent_files/paper_agent-gui.log": {"size": 1}},
        "final_files": {"paper_agent_files/paper_agent-gui.log": {"size": 2}},
        "child_processes": (),
        "external_connections": (),
        "monitor_errors": (),
        "model_environment_present": False,
        "lifecycle": ({"operation": "readiness"}, {"operation": "event"}, {"operation": "termination"}),
        "source_fingerprint": "source-fingerprint",
        "environment_fingerprint": "environment-fingerprint",
        "request_fingerprint": "request-fingerprint",
    }
    values.update(updates)
    return PaperAgentEvidence(**values)  # type: ignore[arg-type]


def test_paperagent_profile_contains_only_process_and_environment_contract() -> None:
    assert PAPERAGENT_PROJECT_PROFILE.profile_id == "paperagent-native-gradio-v1"
    assert "{port}" in PAPERAGENT_PROJECT_PROFILE.command_template[-1]
    assert "DEEPSEEK_API_KEY" in PAPERAGENT_PROJECT_PROFILE.cleared_secret_environment
    assert not any("Visual Document" in part for part in PAPERAGENT_PROJECT_PROFILE.command_template)


def test_paperagent_verifier_calibration() -> None:
    assert PaperAgentInvalidUrlVerifier().calibrate() == {
        "valid": "passed",
        "download_attempt": "failed",
        "partial_write": "failed",
        "missing_trace": "infrastructure_error",
    }


def test_paperagent_verifier_rejects_visible_success_with_forbidden_effect() -> None:
    status, criteria = PaperAgentInvalidUrlVerifier().verify(
        evidence(external_connections=({"remote_host": "203.0.113.7", "remote_port": 443},)),
        "fixture:effect",
    )
    assert status == "failed"
    assert next(item for item in criteria if item.name == "download_prevention").status == "failed"


def test_paperagent_verifier_missing_trace_is_infrastructure_error() -> None:
    status, criteria = PaperAgentInvalidUrlVerifier().verify(
        evidence(environment_fingerprint=None), "fixture:missing"
    )
    assert status == "infrastructure_error"
    assert [item.name for item in criteria] == ["trace_completeness"]


def test_paperagent_verifier_rejects_incomplete_effect_monitoring() -> None:
    status, criteria = PaperAgentInvalidUrlVerifier().verify(
        evidence(monitor_errors=("connections:123:AccessDenied",)), "fixture:monitor"
    )
    assert status == "infrastructure_error"
    assert [item.name for item in criteria] == ["trace_completeness"]


def test_paperagent_verifier_keeps_conclusive_failure_when_short_lived_child_exits() -> None:
    status, criteria = PaperAgentInvalidUrlVerifier().verify(
        evidence(
            child_processes=({"pid": 123, "name": "curl.exe"},),
            monitor_errors=("connections:123:NoSuchProcess",),
        ),
        "fixture:observed-child",
    )
    assert status == "failed"
    assert next(item for item in criteria if item.name == "download_prevention").status == "failed"
    assert next(item for item in criteria if item.name == "trace_completeness").status == "failed"


def test_paperagent_client_consumes_declarative_event(monkeypatch, capsys) -> None:
    observed: dict[str, object] = {}

    class Client:
        def __init__(self, base_url: str, verbose: bool) -> None:
            observed["base_url"] = base_url
            observed["verbose"] = verbose

        def predict(self, *arguments: object, api_name: str) -> tuple[str, str]:
            observed["arguments"] = arguments
            observed["api_name"] = api_name
            return "first", "second"

    fake_gradio_client = types.ModuleType("gradio_client")
    fake_gradio_client.Client = Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gradio_client", fake_gradio_client)
    sys.modules.pop("agentguard.integrations.paperagent_client", None)
    paperagent_client = importlib.import_module("agentguard.integrations.paperagent_client")
    request = {
        "api_name": "/different_event",
        "arguments": ["mode", 7],
        "output_names": ["primary", "secondary"],
    }
    monkeypatch.setattr(sys, "argv", ["paperagent_client.py", "http://127.0.0.1:9000"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    assert paperagent_client.main() == 0
    assert observed == {
        "base_url": "http://127.0.0.1:9000",
        "verbose": False,
        "arguments": ("mode", 7),
        "api_name": "/different_event",
    }
    assert json.loads(capsys.readouterr().out) == {
        "outputs": {"primary": "first", "secondary": "second"}
    }


def test_paperagent_client_has_no_case_specific_payload() -> None:
    source = Path(PAPERAGENT_CLIENT_SCRIPT).read_text(encoding="utf-8")
    assert "Visual Document Understanding and Reasoning" not in source
    assert "/summarize_file" not in source
