import sys
from pathlib import Path

import pytest

from agentguard.integrations.native_command import (
    CommandOperation,
    NativeCommandProfile,
    NativeCommandRunner,
)
from agentguard.targets import TargetInfrastructureError


def write_target(source: Path) -> None:
    source.mkdir()
    (source / "entry.py").write_text(
        """import json, os, sys, time
if '--serve' in sys.argv:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    HTTPServer(('127.0.0.1', int(sys.argv[-1])), Handler).serve_forever()
if '--sleep' in sys.argv:
    time.sleep(2)
if '--text' in sys.argv:
    print('not-json')
    raise SystemExit(0)
if '--large' in sys.argv:
    print('x' * 1000)
    raise SystemExit(0)
payload = json.loads(sys.stdin.read() or '{}')
print(json.dumps({'value': payload.get('value'), 'secret_visible': bool(os.getenv('TARGET_SECRET'))}))
if '--exit-seven' in sys.argv:
    raise SystemExit(7)
""",
        encoding="utf-8",
    )


def test_native_command_runs_argv_without_inheriting_cleared_secret(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    write_target(source)
    monkeypatch.setenv("TARGET_SECRET", "must-not-reach-target")
    runner = NativeCommandRunner(
        Path(sys.executable),
        NativeCommandProfile(
            profile_id="different-command-agent",
            command_template=("{python}", "entry.py"),
            required_source_files=("entry.py",),
            cleared_secret_environment=("TARGET_SECRET",),
        ),
    )
    evidence = runner.run(
        source=source,
        state_path=tmp_path / "state",
        operation=CommandOperation("evaluate", stdin_json={"value": "真实输入"}),
    )
    assert evidence.exit_code == 0
    assert evidence.stdout == {"value": "真实输入", "secret_visible": False}
    assert len(evidence.stdout_sha256) == 64
    assert evidence.trace[0]["operation"] == "evaluate"
    assert runner.environment(source, tmp_path / "state")["TARGET_SECRET"] == ""


def test_native_command_timeout_is_infrastructure_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_target(source)
    runner = NativeCommandRunner(
        Path(sys.executable),
        NativeCommandProfile(
            profile_id="bounded-command-agent",
            command_template=("{python}", "entry.py"),
            required_source_files=("entry.py",),
            timeout_seconds=0.01,
        ),
    )
    with pytest.raises(TargetInfrastructureError, match="TimeoutExpired"):
        runner.run(
            source=source,
            state_path=tmp_path / "state",
            operation=CommandOperation("bounded", arguments=("--sleep",)),
        )


def test_native_command_nonzero_exit_is_verifier_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_target(source)
    runner = NativeCommandRunner(
        Path(sys.executable),
        NativeCommandProfile(
            profile_id="exit-code-agent",
            command_template=("{python}", "entry.py"),
            required_source_files=("entry.py",),
        ),
    )
    evidence = runner.run(
        source=source,
        state_path=tmp_path / "state",
        operation=CommandOperation("expected-failure", arguments=("--exit-seven",)),
    )
    assert evidence.exit_code == 7
    assert evidence.trace[0]["exit_code"] == 7


@pytest.mark.parametrize(
    ("arguments", "profile", "message"),
    [
        (("--text",), {}, "did not return JSON"),
        (("--large",), {"max_output_bytes": 32}, "output exceeded"),
    ],
)
def test_native_command_rejects_uncontracted_output(
    tmp_path: Path,
    arguments: tuple[str, ...],
    profile: dict[str, int],
    message: str,
) -> None:
    source = tmp_path / "source"
    write_target(source)
    runner = NativeCommandRunner(
        Path(sys.executable),
        NativeCommandProfile(
            profile_id="output-contract-agent",
            command_template=("{python}", "entry.py"),
            required_source_files=("entry.py",),
            **profile,
        ),
    )
    with pytest.raises(TargetInfrastructureError, match=message):
        runner.run(
            source=source,
            state_path=tmp_path / "state",
            operation=CommandOperation("invalid-output", arguments=arguments),
        )


def test_native_command_missing_required_source_is_infrastructure_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    runner = NativeCommandRunner(
        Path(sys.executable),
        NativeCommandProfile(
            profile_id="missing-source-agent",
            command_template=("{python}", "entry.py"),
            required_source_files=("entry.py",),
        ),
    )
    with pytest.raises(TargetInfrastructureError, match="Missing native source file"):
        runner.run(
            source=source,
            state_path=tmp_path / "state",
            operation=CommandOperation("missing"),
        )


def test_native_command_service_lifecycle_uses_real_readiness(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_target(source)
    runner = NativeCommandRunner(
        Path(sys.executable),
        NativeCommandProfile(
            profile_id="service-agent",
            command_template=("{python}", "entry.py", "--serve", "{port}"),
            required_source_files=("entry.py",),
        ),
    )
    process, base_url, log_handle = runner.start_service(
        source=source,
        state_path=tmp_path / "state",
        log_path=tmp_path / "service.log",
        readiness_path="/ready",
        label="fixture service",
    )
    assert base_url.startswith("http://127.0.0.1:")
    assert process.poll() is None
    runner.stop_service(process, log_handle)
    assert process.poll() is not None
