from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field, model_validator

from .domain import now
from .domain import ProviderBinding
from .sut_provider import SutProviderConfigurationError, SutProviderEnvironment


class TargetSourceSpec(BaseModel):
    path: str
    revision: str = Field(min_length=7)
    working_tree_fingerprint: str | None = Field(default=None, min_length=16)


class TargetRuntimeSpec(BaseModel):
    kind: Literal["native_http", "native_command"] = "native_http"
    application: str | None = None
    readiness_path: str | None = None
    command: list[str] = Field(default_factory=list)
    required_source_files: list[str] = Field(min_length=1)
    dependency_lock: str | None = None
    python_executable: str | None = None
    environment_templates: dict[str, str] = Field(default_factory=dict)
    cleared_secret_environment: list[str] = Field(default_factory=list)
    operation_timeout_seconds: float = Field(default=30, gt=0)
    startup_timeout_seconds: float = Field(default=30, gt=0)

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
        elif self.readiness_path and not self.readiness_path.startswith("/"):
            raise ValueError("native_command readiness_path must be absolute when declared")
        elif self.readiness_path and not any("{port}" in item for item in self.command):
            raise ValueError("native_command service command requires a {port} placeholder")
        return self


class TargetRuntimeRequirement(BaseModel):
    """A target-owned runtime dependency checked without starting the Agent."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    relative_path: str = Field(min_length=1)
    kind: Literal["file", "directory"] = "file"
    purpose: str = Field(min_length=1)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def reject_path_escape(self) -> "TargetRuntimeRequirement":
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("runtime requirement path must be relative to the target source")
        return self


class TargetProviderInjectionSpec(BaseModel):
    """Non-secret mapping from a sut_native binding into target configuration."""

    api_key_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    model_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    base_url_variable: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    provider_variable: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    provider_values: dict[str, str] = Field(default_factory=dict)
    model_alias_variables: list[str] = Field(default_factory=list)


class TargetTraceSpec(BaseModel):
    """Evidence the target must be able to emit during a live evaluation."""

    trace_path_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    required_event_types: list[str] = Field(min_length=1)
    requires_provider_usage: bool = True


class TargetIsolationSpec(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=list)
    expected_environment_variables: list[str] = Field(default_factory=list)
    mutable_state_paths: list[str] = Field(default_factory=list)
    reset_command: list[str] = Field(default_factory=list)


class TargetManifest(BaseModel):
    schema_version: Literal["1"] = "1"
    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source: TargetSourceSpec
    runtime: TargetRuntimeSpec
    runtime_requirements: list[TargetRuntimeRequirement] = Field(default_factory=list)
    sut_provider: TargetProviderInjectionSpec | None = None
    trace: TargetTraceSpec | None = None
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
    runtime_kind: Literal["python", "executable"] = "python"
    dependency_fingerprint: str
    imported_at: str = Field(default_factory=now)


def _run(command: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def source_working_tree_fingerprint(source: Path) -> str:
    """Fingerprint changed and untracked source files without recording their contents."""
    status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], source)
    entries: list[dict[str, str]] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        relative = line[3:]
        if " -> " in relative:
            relative = relative.rsplit(" -> ", 1)[-1]
        candidate = source / relative
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else "missing"
        entries.append({"status": line[:2], "path": relative.replace("\\", "/"), "sha256": digest})
    encoded = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    runtime_requirements: list[dict[str, object]] | None = None,
    sut_provider: dict[str, object] | None = None,
    trace: dict[str, object] | None = None,
) -> TargetManifest:
    source = source.resolve()
    if not (source / ".git").exists():
        raise ValueError("target source must be a Git working tree")
    revision = _run(["git", "rev-parse", "HEAD"], source)
    manifest = TargetManifest(
        target_id=target_id,
        source=TargetSourceSpec(
            path=str(source), revision=revision, working_tree_fingerprint=source_working_tree_fingerprint(source),
        ),
        runtime=TargetRuntimeSpec(
            kind=kind,
            application=application,
            readiness_path=readiness_path,
            command=command or [],
            required_source_files=required_source_files,
            dependency_lock=dependency_lock,
            python_executable=python_executable,
        ),
        runtime_requirements=[TargetRuntimeRequirement.model_validate(item) for item in runtime_requirements or []],
        sut_provider=TargetProviderInjectionSpec.model_validate(sut_provider) if sut_provider else None,
        trace=TargetTraceSpec.model_validate(trace) if trace else None,
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
    if manifest.source.working_tree_fingerprint:
        observed_tree = source_working_tree_fingerprint(source)
        checks.append({
            "name": "source_working_tree",
            "status": "passed" if observed_tree == manifest.source.working_tree_fingerprint else "failed",
            "detail": f"expected={manifest.source.working_tree_fingerprint}; observed={observed_tree}",
        })
    missing = [item for item in manifest.runtime.required_source_files if not (source / item).is_file()]
    checks.append({
        "name": "required_source_files",
        "status": "passed" if not missing else "failed",
        "detail": f"missing={missing}",
    })
    for requirement in manifest.runtime_requirements:
        candidate = source / requirement.relative_path
        exists = candidate.is_file() if requirement.kind == "file" else candidate.is_dir()
        checksum_matches = (
            requirement.content_sha256 is None
            or (candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest() == requirement.content_sha256)
        )
        checks.append({
            "name": f"runtime_requirement:{requirement.name}",
            "status": "passed" if exists and checksum_matches else "failed",
            "detail": f"purpose={requirement.purpose}; path={candidate}; checksum={'matched' if checksum_matches else 'mismatched'}",
        })
    if manifest.sut_provider:
        provider_mapping_valid = (
            not manifest.sut_provider.provider_variable
            or bool(manifest.sut_provider.provider_values)
        )
        checks.append({
            "name": "sut_provider_mapping",
            "status": "passed" if provider_mapping_valid else "failed",
            "detail": "non-secret target-native provider mapping is declared"
            if provider_mapping_valid else "provider_variable requires provider_values",
        })
    if manifest.trace:
        checks.append({
            "name": "trace_contract",
            "status": "passed",
            "detail": (
                f"path_variable={manifest.trace.trace_path_variable}; "
                f"required_events={sorted(manifest.trace.required_event_types)}; "
                f"provider_usage={manifest.trace.requires_provider_usage}"
            ),
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


def resolve_target_provider_environment(
    manifest: TargetManifest,
    binding: ProviderBinding,
    *,
    credential_reader: Callable[[str], str | None],
) -> dict[str, str]:
    """Resolve one approved binding using only the target's manifest mapping."""
    if not manifest.sut_provider:
        raise SutProviderConfigurationError("Target manifest does not declare a sut_provider mapping.")
    spec = manifest.sut_provider
    return SutProviderEnvironment(
        api_key_variable=spec.api_key_variable,
        model_variable=spec.model_variable,
        base_url_variable=spec.base_url_variable,
        provider_variable=spec.provider_variable,
        provider_values=spec.provider_values,
        model_alias_variables=tuple(spec.model_alias_variables),
    ).resolve(binding, credential_reader=credential_reader)


def verify_target_trace(manifest: TargetManifest, events: list[dict[str, object]]) -> dict[str, object]:
    """Check a target-declared trace contract without trusting agent prose."""
    if not manifest.trace:
        return {"status": "trace_not_declared", "missing_event_types": []}
    observed = {str(item.get("event_type") or "") for item in events}
    missing = sorted(set(manifest.trace.required_event_types).difference(observed))
    usage_complete = True
    if manifest.trace.requires_provider_usage:
        provider_events = [item for item in events if item.get("request_id")]
        usage_complete = bool(provider_events) and all(
            isinstance(item.get("input_tokens"), int)
            and isinstance(item.get("output_tokens"), int)
            and isinstance(item.get("cache_hit_tokens", 0), int)
            for item in provider_events
        )
    return {
        "status": "passed" if not missing and usage_complete else "trace_not_satisfied",
        "missing_event_types": missing,
        "provider_usage_complete": usage_complete,
    }


def _runtime_executable(environment: Path) -> Path:
    if environment.is_file():
        return environment.resolve()
    candidates = [
        environment / "Scripts" / "python.exe", environment / "bin" / "python",
        environment / "node.exe", environment / "bin" / "node",
    ]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), Path())


def _runtime_inventory(executable: Path) -> tuple[str, str]:
    version = subprocess.run([str(executable), "--version"], capture_output=True, text=True, check=True)
    runtime_version = (version.stdout or version.stderr).strip()
    probe = subprocess.run(
        [str(executable), "-c", "import sys; print(sys.implementation.name)"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "cpython":
        return "executable", hashlib.sha256(runtime_version.encode("utf-8")).hexdigest()
    inventory = _run(
        [str(executable), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
        executable.parent,
    )
    return "python", hashlib.sha256(inventory.encode("utf-8")).hexdigest()


class TargetEnvironmentCache:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def import_environment(self, manifest_path: Path, environment: Path) -> EnvironmentCacheRecord:
        inspection = inspect_target_manifest(manifest_path)
        if inspection["status"] != "ready_for_environment_import":
            raise ValueError("target source inspection must pass before environment import")
        manifest = load_target_manifest(manifest_path)
        python = _runtime_executable(environment.resolve())
        if not python.is_file():
            raise ValueError("environment must be a runtime executable or a directory containing Python or Node")
        runtime_kind, dependency_fingerprint = _runtime_inventory(python)
        version = subprocess.run([str(python), "--version"], capture_output=True, text=True, check=True)
        python_version = (version.stdout or version.stderr).strip()
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
            runtime_kind=runtime_kind,
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
