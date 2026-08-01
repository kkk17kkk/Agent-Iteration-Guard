from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import psutil

from ..domain import VerificationCriterion
from ..targets import TargetInfrastructureError
from .native_http import request_json


@dataclass(frozen=True)
class NativeCommandProfile:
    profile_id: str
    command_template: tuple[str, ...]
    required_source_files: tuple[str, ...]
    environment_templates: dict[str, str] = field(default_factory=dict)
    cleared_secret_environment: tuple[str, ...] = ()
    timeout_seconds: float = 30
    max_output_bytes: int = 1024 * 1024

    def command(
        self, python_executable: Path, source: Path, state_path: Path, **variables: object
    ) -> list[str]:
        values = {
            "python": str(python_executable),
            "source": str(source),
            "state_path": str(state_path),
            **{key: str(value) for key, value in variables.items()},
        }
        return [part.format(**values) for part in self.command_template]

    def environment(self, source: Path, state_path: Path, **variables: object) -> dict[str, str]:
        values = {
            "source": str(source),
            "state_path": str(state_path),
            **{key: str(value) for key, value in variables.items()},
        }
        return {key: value.format(**values) for key, value in self.environment_templates.items()}


@dataclass(frozen=True)
class CommandOperation:
    name: str
    arguments: tuple[str, ...] = ()
    stdin_json: object | None = None
    parse_stdout_json: bool = True


@dataclass(frozen=True)
class NativeCommandEvidence:
    operation: str
    exit_code: int
    duration_seconds: float
    stdout: object
    stdout_sha256: str
    stderr_sha256: str
    trace: tuple[dict[str, object], ...]


class CommandVerifierPlugin(Protocol):
    verifier_id: str

    def verify(
        self, evidence: NativeCommandEvidence, evidence_ref: str
    ) -> tuple[str, list[VerificationCriterion]]: ...


class NativeCommandRunner:
    """Run an approved argv template without a shell or target business rules."""

    def __init__(self, python_executable: Path, profile: NativeCommandProfile) -> None:
        self.python_executable = python_executable.resolve()
        self.profile = profile

    def run(self, *, source: Path, state_path: Path, operation: CommandOperation) -> NativeCommandEvidence:
        source = source.resolve()
        state_path = state_path.resolve()
        self._check_source(source)
        environment = self.environment(source, state_path)
        command = [*self.profile.command(self.python_executable, source, state_path), *operation.arguments]
        stdin = None if operation.stdin_json is None else json.dumps(operation.stdin_json, ensure_ascii=False)
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=source,
                env=environment,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.profile.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TargetInfrastructureError(
                f"{self.profile.profile_id} operation {operation.name} failed: {type(error).__name__}"
            ) from error
        duration = time.monotonic() - started
        stdout_bytes = result.stdout.encode("utf-8")
        stderr_bytes = result.stderr.encode("utf-8")
        if len(stdout_bytes) > self.profile.max_output_bytes or len(stderr_bytes) > self.profile.max_output_bytes:
            raise TargetInfrastructureError(f"{self.profile.profile_id} output exceeded the approved limit")
        if operation.parse_stdout_json:
            try:
                stdout: object = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise TargetInfrastructureError(
                    f"{self.profile.profile_id} operation {operation.name} did not return JSON"
                ) from error
        else:
            stdout = result.stdout
        trace = ({
            "operation": operation.name,
            "exit_code": result.returncode,
            "duration_seconds": round(duration, 6),
            "argv_fingerprint": _fingerprint(command),
        },)
        return NativeCommandEvidence(
            operation=operation.name,
            exit_code=result.returncode,
            duration_seconds=duration,
            stdout=stdout,
            stdout_sha256=hashlib.sha256(stdout_bytes).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr_bytes).hexdigest(),
            trace=trace,
        )

    def start_service(
        self,
        *,
        source: Path,
        state_path: Path,
        log_path: Path,
        readiness_path: str,
        label: str,
        startup_timeout_seconds: float = 60,
    ) -> tuple[subprocess.Popen[bytes], str, object]:
        source = source.resolve()
        state_path = state_path.resolve()
        self._check_source(source)
        port = self._free_port()
        environment = self.environment(source, state_path, port=port)
        command = self.profile.command(self.python_executable, source, state_path, port=port)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("wb")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=source,
            env=environment,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log_handle.close()
                raise TargetInfrastructureError(f"{label} exited before readiness; log={log_path}")
            try:
                status, _ = request_json(f"{base_url}{readiness_path}", timeout_seconds=3)
                if status == 200:
                    return process, base_url, log_handle
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        self.stop_service(process, log_handle)
        raise TargetInfrastructureError(f"{label} readiness timed out; log={log_path}")

    @staticmethod
    def stop_service(process: subprocess.Popen[bytes], log_handle: object) -> None:
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
        except psutil.Error:
            descendants = []
        for child in descendants:
            try:
                child.terminate()
            except psutil.Error:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        _, alive = psutil.wait_procs(descendants, timeout=5)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
        close = getattr(log_handle, "close", None)
        if close:
            close()

    def _check_source(self, source: Path) -> None:
        for relative in self.profile.required_source_files:
            if not (source / relative).is_file():
                raise TargetInfrastructureError(
                    f"Missing native source file for {self.profile.profile_id}: {relative}"
                )

    def environment(self, source: Path, state_path: Path, **variables: object) -> dict[str, str]:
        """Build the exact non-secret child environment used by this runner."""
        environment = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC")
            if key in os.environ
        }
        environment["PYTHONUTF8"] = "1"
        environment.update(self.profile.environment(source, state_path, **variables))
        environment.update({key: "" for key in self.profile.cleared_secret_environment})
        return environment

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            return int(candidate.getsockname()[1])


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
