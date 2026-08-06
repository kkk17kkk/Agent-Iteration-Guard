from __future__ import annotations

import os
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .domain import ProviderBinding
from .integrations.native_command import CommandOperation, NativeCommandEvidence, NativeCommandProfile, NativeCommandRunner
from .integrations.native_http import HttpOperation, NativeHttpProcessRunner, NativeHttpProjectProfile
from .target_onboarding import (
    EnvironmentCacheRecord,
    TargetEnvironmentCache,
    TargetManifest,
    load_target_manifest,
    resolve_target_provider_environment,
    verify_target_trace,
)
from .scenario_contracts import ScenarioTraceContract
from .targets import TargetInfrastructureError


@dataclass
class NativeServiceHandle:
    base_url: str
    process: subprocess.Popen[bytes]
    log_handle: object
    _stop: Callable[[subprocess.Popen[bytes], object], None]

    def close(self) -> None:
        self._stop(self.process, self.log_handle)


@dataclass(frozen=True)
class TargetTraceEvidence:
    events: tuple[dict[str, object], ...]
    verification: dict[str, object]


class TargetRuntimeAdapter:
    """Run a manifest-declared, already-installed local target without rebuilding it."""

    def __init__(self, manifest_path: Path, cache_root: Path) -> None:
        self.manifest_path = manifest_path.resolve()
        self.manifest: TargetManifest = load_target_manifest(self.manifest_path)
        preflight = TargetEnvironmentCache(cache_root).preflight(self.manifest_path)
        if preflight["status"] != "onboarding_ready" or not preflight["cache"]:
            raise TargetInfrastructureError("Target runtime adapter requires a passed onboarding preflight.")
        self.cache = EnvironmentCacheRecord.model_validate(preflight["cache"])
        source_path = Path(self.manifest.source.path)
        self.source = (source_path if source_path.is_absolute() else self.manifest_path.parent / source_path).resolve()
        self.runtime_executable = Path(self.cache.python_executable).resolve()
        if not self.runtime_executable.is_file():
            raise TargetInfrastructureError("Imported target runtime executable is no longer available.")

    def reset(self, *, state_path: Path) -> NativeCommandEvidence:
        command = self.manifest.isolation.reset_command
        if not command:
            raise TargetInfrastructureError("Target manifest does not declare a reset command.")
        evidence = self._command_runner(tuple(command)).run(
            source=self.source,
            state_path=state_path,
            operation=CommandOperation(name="reset", parse_stdout_json=False),
        )
        if evidence.exit_code != 0:
            raise TargetInfrastructureError("Declared target reset command returned a non-zero exit code.")
        return evidence

    def run_command(
        self,
        operation: CommandOperation,
        *,
        state_path: Path,
        binding: ProviderBinding | None = None,
        credential_reader: Callable[[str], str | None] = os.getenv,
        trace_path: Path | None = None,
        trial_environment: dict[str, str] | None = None,
        command_template: tuple[str, ...] | None = None,
        command_variables: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> NativeCommandEvidence:
        if self.manifest.runtime.kind != "native_command":
            raise TargetInfrastructureError("run_command requires a native_command target manifest.")
        return self._command_runner(command_template, timeout_seconds=timeout_seconds).run(
            source=self.source,
            state_path=state_path,
            operation=operation,
            environment_overrides=self._overrides(binding, credential_reader, trace_path, trial_environment),
            command_variables=command_variables,
        )

    def start_service(
        self,
        *,
        state_path: Path,
        log_path: Path,
        binding: ProviderBinding | None = None,
        credential_reader: Callable[[str], str | None] = os.getenv,
        trace_path: Path | None = None,
        trial_environment: dict[str, str] | None = None,
    ) -> NativeServiceHandle:
        overrides = self._overrides(binding, credential_reader, trace_path, trial_environment)
        runtime = self.manifest.runtime
        if runtime.kind == "native_http":
            profile = NativeHttpProjectProfile(
                profile_id=self.manifest.target_id,
                application=runtime.application or "",
                readiness_path=runtime.readiness_path or "",
                required_source_files=tuple(runtime.required_source_files),
                environment_templates=runtime.environment_templates,
                cleared_secret_environment=tuple(runtime.cleared_secret_environment),
                startup_timeout_seconds=runtime.startup_timeout_seconds,
            )
            runner = NativeHttpProcessRunner(self.runtime_executable, profile)
            process, base_url, log_handle = runner.start(
                source=self.source, state_path=state_path, log_path=log_path, label=self.manifest.target_id,
                environment_overrides=overrides,
            )
            return NativeServiceHandle(base_url, process, log_handle, runner.stop)
        if not runtime.readiness_path:
            raise TargetInfrastructureError("native_command target needs readiness_path to be started as a service.")
        runner = self._command_runner()
        process, base_url, log_handle = runner.start_service(
            source=self.source,
            state_path=state_path,
            log_path=log_path,
            readiness_path=runtime.readiness_path,
            label=self.manifest.target_id,
            startup_timeout_seconds=runtime.startup_timeout_seconds,
            environment_overrides=overrides,
        )
        return NativeServiceHandle(base_url, process, log_handle, runner.stop_service)

    def execute_http(self, service: NativeServiceHandle, operation: HttpOperation) -> tuple[int, object]:
        if self.manifest.runtime.kind not in {"native_http", "native_command"}:
            raise TargetInfrastructureError("Target manifest does not support HTTP execution.")
        return NativeHttpProcessRunner.execute(service.base_url, operation)

    def read_trace(
        self,
        trace_path: Path,
        *,
        scenario_trace: ScenarioTraceContract | None = None,
    ) -> TargetTraceEvidence:
        if not self.manifest.trace:
            raise TargetInfrastructureError("Target manifest does not declare a trace contract.")
        if not trace_path.is_file():
            raise TargetInfrastructureError("Target trace file was not created.")
        events: list[dict[str, object]] = []
        for line_number, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise TargetInfrastructureError(f"Target trace line {line_number} is not JSON.") from error
            if not isinstance(payload, dict):
                raise TargetInfrastructureError(f"Target trace line {line_number} is not a JSON object.")
            events.append(payload)
        verification = verify_target_trace(self.manifest, events, scenario_trace=scenario_trace)
        return TargetTraceEvidence(tuple(events), verification)

    def _command_runner(
        self,
        command: tuple[str, ...] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> NativeCommandRunner:
        runtime = self.manifest.runtime
        return NativeCommandRunner(
            self.runtime_executable,
            NativeCommandProfile(
                profile_id=self.manifest.target_id,
                command_template=command or tuple(runtime.command),
                required_source_files=tuple(runtime.required_source_files),
                environment_templates=runtime.environment_templates,
                cleared_secret_environment=tuple(runtime.cleared_secret_environment),
                timeout_seconds=timeout_seconds or runtime.operation_timeout_seconds,
            ),
        )

    def _overrides(
        self,
        binding: ProviderBinding | None,
        credential_reader: Callable[[str], str | None],
        trace_path: Path | None,
        trial_environment: dict[str, str] | None,
    ) -> dict[str, str]:
        overrides: dict[str, str] = {}
        if binding:
            overrides.update(resolve_target_provider_environment(
                self.manifest, binding, credential_reader=credential_reader,
            ))
        if trace_path:
            if not self.manifest.trace:
                raise TargetInfrastructureError("Target manifest does not declare a trace contract.")
            overrides[self.manifest.trace.trace_path_variable] = str(trace_path.resolve())
        if trial_environment:
            approved = set(self.manifest.isolation.expected_environment_variables)
            unexpected = set(trial_environment).difference(approved)
            if unexpected:
                raise TargetInfrastructureError(
                    f"Trial environment contains variables absent from the approved manifest: {sorted(unexpected)}"
                )
            if any(not key or not isinstance(value, str) for key, value in trial_environment.items()):
                raise TargetInfrastructureError("Trial environment must contain non-empty names and string values.")
            overrides.update(trial_environment)
        return overrides
