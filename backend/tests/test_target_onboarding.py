import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentguard.cli import main
from agentguard.target_onboarding import (
    TargetEnvironmentCache,
    initialize_target_manifest,
    inspect_target_manifest,
    target_golden_path,
)


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def source_repository(tmp_path: Path) -> Path:
    source = tmp_path / "target"
    source.mkdir()
    run(["git", "init"], source)
    run(["git", "config", "user.email", "test@example.invalid"], source)
    run(["git", "config", "user.name", "AgentGuard Test"], source)
    (source / "app.py").write_text("app = object()\n", encoding="utf-8")
    (source / "requirements.lock").write_text("pydantic==2.10.0\n", encoding="utf-8")
    run(["git", "add", "app.py", "requirements.lock"], source)
    run(["git", "commit", "-m", "fixture"], source)
    return source


def test_clean_source_can_be_onboarded_and_import_existing_environment(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "target.json"
    manifest = initialize_target_manifest(
        source=source,
        output=manifest_path,
        target_id="different-http-agent",
        application="app:app",
        readiness_path="/health",
        required_source_files=["app.py"],
        dependency_lock="requirements.lock",
    )
    assert manifest.source.revision == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert inspect_target_manifest(manifest_path)["status"] == "ready_for_environment_import"

    cache = TargetEnvironmentCache(tmp_path / "cache")
    record = cache.import_environment(manifest_path, Path(sys.executable))
    assert record.source_revision == manifest.source.revision
    assert record.dependency_fingerprint
    assert cache.preflight(manifest_path)["status"] == "onboarding_ready"
    golden = target_golden_path(manifest_path, tmp_path / "cache")
    assert golden["status"] == "ready_for_case_contracts"
    assert golden["release_status"] == "not_evaluated"
    assert len(golden["required_next_steps"]) == 6
    payload = json.loads(next((tmp_path / "cache").glob("*.json")).read_text(encoding="utf-8"))
    assert "environment_variables" not in payload


def test_source_revision_drift_fails_preflight(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "target.json"
    initialize_target_manifest(
        source=source,
        output=manifest_path,
        target_id="revision-bound-target",
        application="app:app",
        readiness_path="/health",
        required_source_files=["app.py"],
    )
    (source / "app.py").write_text("app = 'changed'\n", encoding="utf-8")
    run(["git", "add", "app.py"], source)
    run(["git", "commit", "-m", "drift"], source)
    assert inspect_target_manifest(manifest_path)["status"] == "source_not_satisfied"


def test_native_command_manifest_has_no_http_fields(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    manifest = initialize_target_manifest(
        source=source,
        output=tmp_path / "command-target.json",
        target_id="different-cli-agent",
        kind="native_command",
        command=["{python}", "app.py", "eval"],
        required_source_files=["app.py"],
    )
    assert manifest.runtime.kind == "native_command"
    assert manifest.runtime.command == ["{python}", "app.py", "eval"]
    assert manifest.runtime.application is None
    assert manifest.runtime.readiness_path is None


def test_target_cli_json_golden_path_fails_with_structured_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "target.json"
    db_path = tmp_path / "agentguard.db"
    common = ["--db", str(db_path), "--format", "json", "target"]
    code = main([
        *common,
        "init",
        "--source", str(source),
        "--target-id", "cli-agent",
        "--kind", "native_command",
        "--command-part", "{python}",
        "--command-part=-m",
        "--command-part", "app",
        "--required-file", "app.py",
        "--output", str(manifest_path),
    ])
    created = json.loads(capsys.readouterr().out)
    assert code == 0
    assert created["data"]["runtime"]["kind"] == "native_command"

    code = main([*common, "preflight", "--manifest", str(manifest_path), "--cache-root", str(tmp_path / "cache")])
    preflight = json.loads(capsys.readouterr().out)
    assert code == 0
    assert preflight["data"]["status"] == "environment_not_satisfied"

    code = main([*common, "golden-path", "--manifest", str(manifest_path), "--cache-root", str(tmp_path / "cache")])
    failure = json.loads(capsys.readouterr().out)
    assert code == 3
    assert failure == {
        "ok": False,
        "error": {
            "stage": "target_onboarding",
            "reason": "target golden path requires a clean revision and imported environment",
        },
    }


def test_target_cli_text_inspection_is_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "target.json"
    initialize_target_manifest(
        source=source,
        output=manifest_path,
        target_id="text-agent",
        kind="native_command",
        command=["{python}", "app.py"],
        required_source_files=["app.py"],
    )
    code = main([
        "--db", str(tmp_path / "agentguard.db"),
        "target", "inspect", "--manifest", str(manifest_path),
    ])
    output = capsys.readouterr().out
    assert code == 0
    assert "ready_for_environment_import" in output
    assert "source_revision" in output
