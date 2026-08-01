from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .domain import now


class TargetSourceSpec(BaseModel):
    path: str
    revision: str = Field(min_length=7)


class TargetRuntimeSpec(BaseModel):
    kind: Literal["native_http", "native_command"] = "native_http"
    application: str | None = None
    readiness_path: str | None = None
    command: list[str] = Field(default_factory=list)
    required_source_files: list[str] = Field(min_length=1)
    dependency_lock: str | None = None
    python_executable: str | None = None

    @model_validator(mode="after")
    def validate_runtime_shape(self) -> "TargetRuntimeSpec":
        if self.kind == "native_http":
            if not self.application or len(self.application) < 3:
                raise ValueError("native_http runtime requires application")
            if not self.readiness_path or not self.readiness_path.startswith("/"):
                raise ValueError("native_http runtime requires an absolute readiness_path")
            if self.command:
                raise ValueError("native_http runtime does not accept command parts")
        elif not self.command:
            raise ValueError("native_command runtime requires command parts")
        return self


class TargetIsolationSpec(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=list)
    expected_environment_variables: list[str] = Field(default_factory=list)
    mutable_state_paths: list[str] = Field(default_factory=list)


class TargetManifest(BaseModel):
    schema_version: Literal["1"] = "1"
    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source: TargetSourceSpec
    runtime: TargetRuntimeSpec
    isolation: TargetIsolationSpec = Field(default_factory=TargetIsolationSpec)

    def fingerprint(self) -> str:
        encoded = json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class EnvironmentCacheRecord(BaseModel):
    cache_id: str
    target_id: str
    source_revision: str
    manifest_fingerprint: str
    environment_path: str
    python_executable: str
    python_version: str
    dependency_fingerprint: str
    imported_at: str = Field(default_factory=now)


def _run(command: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _resolve(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_target_manifest(path: Path) -> TargetManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("target manifest must be a JSON object")
    return TargetManifest.model_validate(payload)


def initialize_target_manifest(
    *,
    source: Path,
    output: Path,
    target_id: str,
    kind: Literal["native_http", "native_command"] = "native_http",
    application: str | None = None,
    readiness_path: str | None = None,
    command: list[str] | None = None,
    required_source_files: list[str],
    dependency_lock: str | None = None,
    python_executable: str | None = None,
) -> TargetManifest:
    source = source.resolve()
    if not (source / ".git").exists():
        raise ValueError("target source must be a Git working tree")
    revision = _run(["git", "rev-parse", "HEAD"], source)
    manifest = TargetManifest(
        target_id=target_id,
        source=TargetSourceSpec(path=str(source), revision=revision),
        runtime=TargetRuntimeSpec(
            kind=kind,
            application=application,
            readiness_path=readiness_path,
            command=command or [],
            required_source_files=required_source_files,
            dependency_lock=dependency_lock,
            python_executable=python_executable,
        ),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def inspect_target_manifest(path: Path) -> dict[str, object]:
    manifest = load_target_manifest(path)
    source = _resolve(path.parent, manifest.source.path)
    if source is None or not source.is_dir():
        raise ValueError("target source path does not exist")
    observed_revision = _run(["git", "rev-parse", "HEAD"], source)
    checks: list[dict[str, str]] = [{
        "name": "source_revision",
        "status": "passed" if observed_revision == manifest.source.revision else "failed",
        "detail": f"expected={manifest.source.revision}; observed={observed_revision}",
    }]
    missing = [item for item in manifest.runtime.required_source_files if not (source / item).is_file()]
    checks.append({
        "name": "required_source_files",
        "status": "passed" if not missing else "failed",
        "detail": f"missing={missing}",
    })
    dependency_lock = _resolve(source, manifest.runtime.dependency_lock)
    if manifest.runtime.dependency_lock:
        checks.append({
            "name": "dependency_lock",
            "status": "passed" if dependency_lock and dependency_lock.is_file() else "failed",
            "detail": str(dependency_lock),
        })
    status = "ready_for_environment_import" if all(item["status"] == "passed" for item in checks) else "source_not_satisfied"
    return {
        "target_id": manifest.target_id,
        "manifest_fingerprint": manifest.fingerprint(),
        "source": str(source),
        "revision": observed_revision,
        "status": status,
        "checks": checks,
    }


def _python_in_environment(environment: Path) -> Path:
    if environment.is_file():
        return environment.resolve()
    candidates = [environment / "Scripts" / "python.exe", environment / "bin" / "python"]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), Path())


class TargetEnvironmentCache:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def import_environment(self, manifest_path: Path, environment: Path) -> EnvironmentCacheRecord:
        inspection = inspect_target_manifest(manifest_path)
        if inspection["status"] != "ready_for_environment_import":
            raise ValueError("target source inspection must pass before environment import")
        manifest = load_target_manifest(manifest_path)
        python = _python_in_environment(environment.resolve())
        if not python.is_file():
            raise ValueError("environment must be a Python executable or a venv containing Python")
        version = subprocess.run([str(python), "--version"], capture_output=True, text=True, check=True)
        python_version = (version.stdout or version.stderr).strip()
        # `pip freeze` resolves editable installs and can fail on a stale or
        # non-ASCII project path. `pip list` gives the installed environment
        # inventory needed for this content fingerprint without dereferencing it.
        inventory = _run(
            [str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            python.parent,
        )
        dependency_fingerprint = hashlib.sha256(inventory.encode("utf-8")).hexdigest()
        identity = {
            "target_id": manifest.target_id,
            "revision": manifest.source.revision,
            "manifest": manifest.fingerprint(),
            "python_version": python_version,
            "dependencies": dependency_fingerprint,
        }
        cache_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        record = EnvironmentCacheRecord(
            cache_id=cache_id,
            target_id=manifest.target_id,
            source_revision=manifest.source.revision,
            manifest_fingerprint=manifest.fingerprint(),
            environment_path=str(environment.resolve()),
            python_executable=str(python),
            python_version=python_version,
            dependency_fingerprint=dependency_fingerprint,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{cache_id}.json").write_text(
            json.dumps(record.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return record

    def records(self, target_id: str) -> list[EnvironmentCacheRecord]:
        if not self.root.exists():
            return []
        records = []
        for path in sorted(self.root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = EnvironmentCacheRecord.model_validate(payload)
            if record.target_id == target_id:
                records.append(record)
        return records

    def preflight(self, manifest_path: Path) -> dict[str, object]:
        inspection = inspect_target_manifest(manifest_path)
        manifest = load_target_manifest(manifest_path)
        matching = [
            item for item in self.records(manifest.target_id)
            if item.source_revision == manifest.source.revision
            and item.manifest_fingerprint == manifest.fingerprint()
        ]
        cache = matching[-1] if matching else None
        cache_ok = bool(cache and Path(cache.python_executable).is_file())
        checks = [*inspection["checks"], {
            "name": "environment_cache",
            "status": "passed" if cache_ok else "failed",
            "detail": cache.cache_id if cache else "no matching imported environment",
        }]
        return {
            "target_id": manifest.target_id,
            "status": "onboarding_ready" if all(item["status"] == "passed" for item in checks) else "environment_not_satisfied",
            "manifest_fingerprint": manifest.fingerprint(),
            "cache": cache.model_dump() if cache else None,
            "checks": checks,
            "next_step": "approve case contracts and ProviderBinding" if cache_ok else "import a verified local environment",
        }


def target_golden_path(manifest_path: Path, cache_root: Path) -> dict[str, object]:
    """Close local onboarding and expose the remaining evidence-producing steps."""
    preflight = TargetEnvironmentCache(cache_root).preflight(manifest_path)
    if preflight["status"] != "onboarding_ready":
        raise ValueError("target golden path requires a clean revision and imported environment")
    return {
        "target_id": preflight["target_id"],
        "status": "ready_for_case_contracts",
        "manifest_fingerprint": preflight["manifest_fingerprint"],
        "environment_cache_id": preflight["cache"]["cache_id"],
        "required_next_steps": [
            "approve harness, environment, and task-verifier contracts",
            "register the four case contracts and deterministic preflight evidence",
            "bind a non-secret control_plane ProviderBinding and runtime budget",
            "run the real control-plane Agent; never substitute a deterministic fallback",
            "execute the approved revision pair and independent Verifier",
            "build immutable ReportManifest before the zh-CN report Agent",
        ],
        "release_status": "not_evaluated",
    }
