from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard import api


def _first_run_package() -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("new-agent/agent.py", "print('agent')\n")
        archive.writestr("new-agent/skills/brief/SKILL.md", "# Brief\nWrite a concise brief.\n")
        archive.writestr("new-agent/tools/notes.py", "def note_tool(value):\n    return value\n")
        archive.writestr("new-agent/evaluate.py", "# reviewed interaction adapter\n")
        archive.writestr("new-agent/oracle.py", "# reviewed independent oracle\n")
        archive.writestr("new-agent/requirements.txt", "\n")
    return content.getvalue()


def test_first_run_provider_and_runtime_onboarding_are_gui_owned(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "onboarding.db"))
    monkeypatch.setenv("AGENTGUARD_UPLOAD_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("AGENTGUARD_RUNTIME_SOURCE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("OPENAI_API_KEY", "not-returned-to-client")
    client = TestClient(api.app)

    upload = client.post(
        "/api/v1/projects/first-run-agent/uploads",
        files={"file": ("first-run.zip", _first_run_package(), "application/zip")},
        data={"source_kind": "package"},
    )
    assert upload.status_code == 200, upload.text
    scan = client.post("/api/v1/projects/first-run-agent/scan", json={
        "source_kind": "package", "source_ref": upload.json()["source_ref"], "version": "initial",
    })
    assert scan.status_code == 200, scan.text

    credentials = client.get("/api/v1/projects/first-run-agent/provider-credentials")
    assert credentials.status_code == 200
    assert {item["provider"] for item in credentials.json()} == {"openai", "deepseek", "vllm"}
    assert "not-returned-to-client" not in credentials.text
    binding = client.post("/api/v1/projects/first-run-agent/provider-bindings/onboard", json={
        "provider": "openai", "model": "gpt-test", "credential_environment_variable": "OPENAI_API_KEY",
    })
    assert binding.status_code == 200, binding.text
    assert binding.json()["status"] == "available"
    assert "credential_source_ref" not in binding.json()

    draft = client.get("/api/v1/projects/first-run-agent/runtime-drafts?snapshot_version=initial")
    assert draft.status_code == 200, draft.text
    assert draft.json()["entrypoint"] == "python agent.py"
    assert draft.json()["suggested_interaction_command"] == ["{python}", "evaluate.py"]
    assert draft.json()["suggested_oracle_command"] == ["{python}", "oracle.py"]
    saved = client.post(f"/api/v1/projects/first-run-agent/runtime-drafts/{draft.json()['draft_id']}/save", json={
        "name": "first run runtime", "entrypoint": "python agent.py", "working_directory": ".",
        "interaction_command": ["{python}", "evaluate.py"], "oracle_command": ["{python}", "oracle.py"],
        "oracle_id": "first-run-oracle",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["snapshot_version"] == "initial"
    assert "manifest_path" not in saved.json()
    assert Path(tmp_path / "runtime" / "sources" / "first-run-agent").exists()
