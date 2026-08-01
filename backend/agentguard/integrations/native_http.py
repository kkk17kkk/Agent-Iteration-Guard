from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..domain import VerificationCriterion
from ..targets import TargetInfrastructureError


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_sqlite_database(path: Path) -> dict[str, list[dict[str, object]]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables
        }
    finally:
        connection.close()


def request_json(
    url: str,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 10,
) -> tuple[int, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = {"body_fingerprint": hashlib.sha256(body.encode("utf-8")).hexdigest()}
        return error.code, parsed


@dataclass(frozen=True)
class HttpOperation:
    name: str
    method: str
    path: str
    payload: dict[str, object] | None = None


@dataclass(frozen=True)
class NativeHttpProjectProfile:
    profile_id: str
    application: str
    readiness_path: str
    required_source_files: tuple[str, ...]
    environment_templates: dict[str, str] = field(default_factory=dict)
    cleared_secret_environment: tuple[str, ...] = ()
    startup_timeout_seconds: float = 20

    def environment(self, source: Path, state_path: Path) -> dict[str, str]:
        values = {"source": str(source), "state_path": str(state_path)}
        return {key: value.format(**values) for key, value in self.environment_templates.items()}


@dataclass(frozen=True)
class DeclarativeHttpCase:
    case_id: str
    setup_operations: tuple[HttpOperation, ...]
    trial_operation: HttpOperation
    catalog_relative_path: str | None = None


@dataclass(frozen=True)
class NativeHttpEvidence:
    response_status: int
    response: object
    initial_state: dict[str, list[dict[str, object]]]
    final_state: dict[str, list[dict[str, object]]]
    trace: list[dict[str, object]]
    catalog: object | None = None


class TrialVerifierPlugin(Protocol):
    verifier_id: str

    def verify(
        self, evidence: NativeHttpEvidence, evidence_ref: str
    ) -> tuple[str, list[VerificationCriterion]]: ...

    def calibrate(
        self, initial_state: dict[str, list[dict[str, object]]], catalog: object | None
    ) -> dict[str, str]: ...


class NativeHttpProcessRunner:
    """Reusable native-process lifecycle; it contains no target business rules."""

    def __init__(self, python_executable: Path, profile: NativeHttpProjectProfile) -> None:
        self.python_executable = python_executable
        self.profile = profile

    def start(
        self,
        *,
        source: Path,
        state_path: Path,
        log_path: Path,
        label: str,
    ) -> tuple[subprocess.Popen[bytes], str, object]:
        for relative in self.profile.required_source_files:
            if not (source / relative).is_file():
                raise TargetInfrastructureError(f"Missing native source file for {self.profile.profile_id}: {relative}")
        port = self._free_port()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("wb")
        environment = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC")
            if key in os.environ
        }
        environment.update({"PYTHONUTF8": "1"})
        environment.update(self.profile.environment(source, state_path))
        environment.update({key: "" for key in self.profile.cleared_secret_environment})
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [
                str(self.python_executable),
                "-m",
                "uvicorn",
                self.profile.application,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=source,
            env=environment,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + self.profile.startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log_handle.close()
                raise TargetInfrastructureError(f"{label} exited before readiness; log={log_path}")
            try:
                status, _ = request_json(f"{base_url}{self.profile.readiness_path}")
                if status == 200:
                    return process, base_url, log_handle
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        self.stop(process, log_handle)
        raise TargetInfrastructureError(f"{label} readiness timed out; log={log_path}")

    @staticmethod
    def execute(base_url: str, operation: HttpOperation) -> tuple[int, object]:
        return request_json(f"{base_url}{operation.path}", operation.method, operation.payload)

    @staticmethod
    def stop(process: subprocess.Popen[bytes], log_handle: object) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        close = getattr(log_handle, "close", None)
        if close:
            close()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            return int(candidate.getsockname()[1])
