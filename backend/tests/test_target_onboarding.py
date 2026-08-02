import json
import subprocess
import sys
import textwrap
import shutil
from pathlib import Path

import pytest

from agentguard.cli import main
from agentguard.target_onboarding import (
    TargetEnvironmentCache,
    initialize_target_manifest,
    inspect_target_manifest,
    load_target_manifest,
    resolve_target_provider_environment,
    target_golden_path,
    verify_target_trace,
)
from agentguard.domain import ProviderBinding
from agentguard.integrations.native_http import HttpOperation
from agentguard.target_runtime import TargetRuntimeAdapter
from agentguard.targets import TargetInfrastructureError


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


def test_existing_non_python_runtime_can_be_imported_without_redeployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "target.json"
    initialize_target_manifest(
        source=source, output=manifest_path, target_id="existing-runtime-agent",
        kind="native_command", command=["{python}", "app.py"], required_source_files=["app.py"],
    )
    executable = tmp_path / "runtime.exe"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr("agentguard.target_onboarding._runtime_inventory", lambda _: ("executable", "a" * 64))
    original_run = subprocess.run

    def runtime_version(command, *args, **kwargs):
        if command[0] == str(executable):
            return subprocess.CompletedProcess(command, 0, stdout="v1.2.3", stderr="")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(
        "agentguard.target_onboarding.subprocess.run",
        runtime_version,
    )

    record = TargetEnvironmentCache(tmp_path / "cache").import_environment(manifest_path, executable)

    assert record.runtime_kind == "executable"
    assert record.python_executable == str(executable.resolve())


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


def test_uncommitted_target_source_change_fails_manifest_preflight(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "target.json"
    initialize_target_manifest(
        source=source, output=manifest_path, target_id="working-tree-bound-target",
        application="app:app", readiness_path="/health", required_source_files=["app.py"],
    )
    (source / "local_instrumentation.py").write_text("trace = True\n", encoding="utf-8")

    inspection = inspect_target_manifest(manifest_path)

    assert inspection["status"] == "source_not_satisfied"
    assert next(item for item in inspection["checks"] if item["name"] == "source_working_tree")["status"] == "failed"


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


def test_manifest_preflight_checks_declared_runtime_data_and_live_evidence_capabilities(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    (source / "data").mkdir()
    (source / "data" / "seed.json").write_text("{}", encoding="utf-8")
    manifest_path = tmp_path / "runtime-target.json"
    payload = {
        "target_id": "runtime-contract-agent",
        "source": {"path": str(source), "revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
        ).stdout.strip()},
        "runtime": {"kind": "native_http", "application": "app:app", "readiness_path": "/health", "required_source_files": ["app.py"]},
        "runtime_requirements": [{"name": "task-data", "relative_path": "data/seed.json", "kind": "file", "purpose": "real task input"}],
        "sut_provider": {"api_key_variable": "TARGET_API_KEY", "model_variable": "TARGET_MODEL", "base_url_variable": "TARGET_BASE_URL"},
        "trace": {"trace_path_variable": "AGENTGUARD_TRACE_PATH", "required_event_types": ["provider_completed", "skill_completed"]},
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    inspection = inspect_target_manifest(manifest_path)

    assert inspection["status"] == "ready_for_environment_import"
    assert {item["name"] for item in inspection["checks"]} >= {
        "runtime_requirement:task-data", "sut_provider_mapping", "trace_contract",
    }
    (source / "data" / "seed.json").unlink()
    assert inspect_target_manifest(manifest_path)["status"] == "source_not_satisfied"


def test_manifest_rejects_runtime_requirement_outside_target_source(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    payload = {
        "target_id": "unsafe-contract-agent",
        "source": {"path": str(source), "revision": "1234567"},
        "runtime": {"application": "app:app", "readiness_path": "/health", "required_source_files": ["app.py"]},
        "runtime_requirements": [{"name": "escape", "relative_path": "../secret", "purpose": "unsafe"}],
    }
    with pytest.raises(ValueError, match="relative to the target source"):
        from agentguard.target_onboarding import TargetManifest
        TargetManifest.model_validate(payload)


def test_manifest_provider_mapping_and_trace_contract_are_target_neutral(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "target.json"
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True).stdout.strip()
    manifest_path.write_text(json.dumps({
        "target_id": "portable-agent", "source": {"path": str(source), "revision": revision},
        "runtime": {"application": "app:app", "readiness_path": "/health", "required_source_files": ["app.py"]},
        "sut_provider": {"api_key_variable": "TARGET_KEY", "model_variable": "TARGET_MODEL", "base_url_variable": "TARGET_URL", "model_alias_variables": ["TARGET_FAST_MODEL"]},
        "trace": {"trace_path_variable": "TRACE_FILE", "required_event_types": ["skill_started", "provider_completed"]},
    }), encoding="utf-8")
    binding = ProviderBinding(
        project_id="portable", role="sut_native", provider="deepseek", base_url="https://api.deepseek.com/v1",
        model="deepseek-chat", expected_environment_variable="DEEPSEEK_API_KEY", credential_source_ref="runtime",
        batch_budget_usd=0.1, timeout_seconds=30, allowed_hosts=["api.deepseek.com"], data_retention_policy="none",
    )
    environment = resolve_target_provider_environment(
        load_target_manifest(manifest_path), binding, credential_reader=lambda _: "runtime-secret",
    )

    assert environment == {
        "TARGET_KEY": "runtime-secret", "TARGET_MODEL": "deepseek-chat", "TARGET_URL": "https://api.deepseek.com/v1",
        "TARGET_FAST_MODEL": "deepseek-chat",
    }
    manifest = load_target_manifest(manifest_path)
    assert verify_target_trace(manifest, [
        {"event_type": "skill_started"},
        {"event_type": "provider_completed", "request_id": "r1", "input_tokens": 10, "output_tokens": 2, "cache_hit_tokens": 0},
    ])["status"] == "passed"
    assert verify_target_trace(manifest, [
        {"event_type": "skill_started"},
        {"event_type": "provider_completed", "request_id": "r1"},
    ])["status"] == "trace_not_satisfied"


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


def test_target_init_cli_writes_portable_runtime_contract_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = source_repository(tmp_path)
    provider_path = tmp_path / "provider.json"
    trace_path = tmp_path / "trace.json"
    provider_path.write_text(json.dumps({"api_key_variable": "TARGET_KEY", "model_variable": "TARGET_MODEL"}), encoding="utf-8")
    trace_path.write_text(json.dumps({"trace_path_variable": "TRACE_PATH", "required_event_types": ["skill_started"]}), encoding="utf-8")
    manifest_path = tmp_path / "portable.json"

    assert main([
        "--db", str(tmp_path / "agentguard.db"), "--format", "json", "target", "init",
        "--source", str(source), "--target-id", "portable-cli-agent", "--application", "app:app",
        "--readiness-path", "/health", "--required-file", "app.py",
        "--runtime-requirement", '{"name":"task-data","relative_path":"requirements.lock","purpose":"task input"}',
        "--sut-provider", str(provider_path), "--trace", str(trace_path), "--output", str(manifest_path),
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["runtime_requirements"][0]["name"] == "task-data"
    assert payload["data"]["sut_provider"]["api_key_variable"] == "TARGET_KEY"
    assert payload["data"]["trace"]["required_event_types"] == ["skill_started"]


def test_manifest_runtime_adapter_starts_existing_command_service_without_redeployment(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    service = source / "service.py"
    service.write_text(textwrap.dedent("""
        import argparse
        import json
        from http.server import BaseHTTPRequestHandler, HTTPServer

        parser = argparse.ArgumentParser()
        parser.add_argument('--port', type=int, required=True)
        port = parser.parse_args().port

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'ready': self.path == '/ready'}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, format, *args):
                pass

        HTTPServer(('127.0.0.1', port), Handler).serve_forever()
    """), encoding="utf-8")
    run(["git", "add", "service.py"], source)
    run(["git", "commit", "-m", "service entrypoint"], source)
    manifest_path = tmp_path / "service-target.json"
    initialize_target_manifest(
        source=source,
        output=manifest_path,
        target_id="portable-command-http",
        kind="native_command",
        command=["{runtime}", "service.py", "--port", "{port}"],
        readiness_path="/ready",
        required_source_files=["service.py"],
    )
    cache_root = tmp_path / "cache"
    TargetEnvironmentCache(cache_root).import_environment(manifest_path, Path(sys.executable))
    adapter = TargetRuntimeAdapter(manifest_path, cache_root)
    state = tmp_path / "state"
    handle = adapter.start_service(state_path=state, log_path=tmp_path / "target.log")
    try:
        status, body = adapter.execute_http(handle, HttpOperation("ready", "GET", "/ready"))
    finally:
        handle.close()

    assert status == 200
    assert body == {"ready": True}


def test_target_probe_cli_uses_declared_readiness_without_provider_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = source_repository(tmp_path)
    service = source / "service.py"
    service.write_text(textwrap.dedent("""
        import argparse, json
        from http.server import BaseHTTPRequestHandler, HTTPServer
        parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, required=True)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'ok': self.path == '/ready'}).encode('utf-8')
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
            def log_message(self, format, *args): pass
        HTTPServer(('127.0.0.1', parser.parse_args().port), Handler).serve_forever()
    """), encoding="utf-8")
    run(["git", "add", "service.py"], source)
    run(["git", "commit", "-m", "probe service"], source)
    manifest_path = tmp_path / "probe.json"
    initialize_target_manifest(
        source=source, output=manifest_path, target_id="probe-command-target", kind="native_command",
        command=["{runtime}", "service.py", "--port", "{port}"], readiness_path="/ready",
        required_source_files=["service.py"],
    )
    cache_root = tmp_path / "cache"
    TargetEnvironmentCache(cache_root).import_environment(manifest_path, Path(sys.executable))

    assert main([
        "--db", str(tmp_path / "agentguard.db"), "--format", "json", "target", "probe",
        "--manifest", str(manifest_path), "--cache-root", str(cache_root),
        "--state-path", str(tmp_path / "state.db"), "--log-path", str(tmp_path / "probe.log"),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {
        "status": 200, "body": {"ok": True}, "readiness_path": "/ready",
    }


def test_manifest_runtime_adapter_reads_and_validates_target_owned_trace(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "trace-target.json"
    initialize_target_manifest(
        source=source, output=manifest_path, target_id="trace-contract-agent",
        application="app:app", readiness_path="/health", required_source_files=["app.py"],
        trace={"trace_path_variable": "TRACE_PATH", "required_event_types": ["skill_started", "provider_completed"]},
    )
    cache_root = tmp_path / "cache"
    TargetEnvironmentCache(cache_root).import_environment(manifest_path, Path(sys.executable))
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"event_type":"skill_started"}\n'
        '{"event_type":"provider_completed","request_id":"r1","input_tokens":4,"output_tokens":2,"cache_hit_tokens":0}\n',
        encoding="utf-8",
    )

    evidence = TargetRuntimeAdapter(manifest_path, cache_root).read_trace(trace_path)

    assert evidence.verification["status"] == "passed"
    trace_path.write_text('{"event_type":"skill_started"}\n', encoding="utf-8")
    assert TargetRuntimeAdapter(manifest_path, cache_root).read_trace(trace_path).verification["status"] == "trace_not_satisfied"


def test_runtime_adapter_allows_only_manifest_approved_trial_environment(tmp_path: Path) -> None:
    source = source_repository(tmp_path)
    manifest_path = tmp_path / "trial-environment.json"
    initialize_target_manifest(
        source=source, output=manifest_path, target_id="trial-environment-agent",
        application="app:app", readiness_path="/health", required_source_files=["app.py"],
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["isolation"]["expected_environment_variables"] = ["AGENTGUARD_INTERVENTION"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    cache_root = tmp_path / "cache"
    TargetEnvironmentCache(cache_root).import_environment(manifest_path, Path(sys.executable))
    adapter = TargetRuntimeAdapter(manifest_path, cache_root)

    assert adapter._overrides(None, lambda _: None, None, {"AGENTGUARD_INTERVENTION": "removed"}) == {
        "AGENTGUARD_INTERVENTION": "removed"
    }
    with pytest.raises(TargetInfrastructureError, match="absent from the approved manifest"):
        adapter._overrides(None, lambda _: None, None, {"UNAPPROVED": "value"})


def test_manifest_runtime_adapter_can_reuse_an_existing_node_http_runtime(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed in this test environment")
    source = source_repository(tmp_path)
    (source / "service.js").write_text(textwrap.dedent("""
        const http = require('http');
        const port = Number(process.argv[process.argv.indexOf('--port') + 1]);
        http.createServer((req, res) => {
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify({ready: req.url === '/ready'}));
        }).listen(port, '127.0.0.1');
    """), encoding="utf-8")
    run(["git", "add", "service.js"], source)
    run(["git", "commit", "-m", "node service entrypoint"], source)
    manifest_path = tmp_path / "node-target.json"
    initialize_target_manifest(
        source=source, output=manifest_path, target_id="portable-node-http", kind="native_command",
        command=["{runtime}", "service.js", "--port", "{port}"], readiness_path="/ready",
        required_source_files=["service.js"],
    )
    cache_root = tmp_path / "cache"
    TargetEnvironmentCache(cache_root).import_environment(manifest_path, Path(node))
    adapter = TargetRuntimeAdapter(manifest_path, cache_root)
    handle = adapter.start_service(state_path=tmp_path / "state", log_path=tmp_path / "node.log")
    try:
        status, body = adapter.execute_http(handle, HttpOperation("ready", "GET", "/ready"))
    finally:
        handle.close()

    assert status == 200
    assert body == {"ready": True}


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
