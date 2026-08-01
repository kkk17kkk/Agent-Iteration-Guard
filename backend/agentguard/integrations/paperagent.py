from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import psutil

from ..domain import (
    EnvironmentCheck,
    EvolutionTrial,
    EvolutionVerification,
    ProviderBinding,
    RuntimeEnvironmentPreflight,
)
from ..store import Store
from ..targets import TargetInfrastructureError, TargetObservation
from .native_command import NativeCommandProfile, NativeCommandRunner
from .paperagent_case import (
    PAPERAGENT_INVALID_URL_CASE,
    DeclarativeGradioCase,
    PaperAgentEvidence,
    PaperAgentInvalidUrlVerifier,
)
from .paperagent_profile import (
    PAPERAGENT_CLIENT_SCRIPT,
    PAPERAGENT_PROJECT_PROFILE,
    PAPERAGENT_READINESS_PATH,
)


@dataclass(frozen=True)
class PaperAgentRevision:
    role: str
    revision_id: str
    commit_sha: str
    source: Path


@dataclass(frozen=True)
class PaperAgentCaseConfig:
    project_id: str
    evolution_case_id: str
    environment_contract_id: str
    control_plane_binding_id: str
    run_root: Path
    python_executable: Path
    dependency_lock: Path
    wheelhouse: Path
    baseline: PaperAgentRevision
    candidate: PaperAgentRevision
    contract_refs: tuple[str, ...]
    changeset_and_memory_refs: tuple[str, ...] = ()


class _EffectMonitor:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.errors: set[str] = set()
        try:
            self._existing_child_pids = {
                child.pid for child in psutil.Process(pid).children(recursive=True)
            }
        except psutil.Error as error:
            self._existing_child_pids = set()
            self.errors.add(f"initial_process_tree:{type(error).__name__}")
        self.children: dict[int, dict[str, object]] = {}
        self.external: dict[str, dict[str, object]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._sample()

    def _sample_loop(self) -> None:
        while not self._stop.wait(0.01):
            self._sample()

    def _sample(self) -> None:
        try:
            target = psutil.Process(self.pid)
            processes = [target, *target.children(recursive=True)]
        except psutil.Error as error:
            self.errors.add(f"process_tree:{type(error).__name__}")
            return
        for process in processes[1:]:
            if process.pid in self._existing_child_pids:
                continue
            try:
                self.children[process.pid] = {"pid": process.pid, "name": process.name()}
            except psutil.Error:
                self.children[process.pid] = {"pid": process.pid, "name": "unavailable"}
        for process in processes:
            try:
                connections = process.net_connections(kind="inet")
            except psutil.Error as error:
                self.errors.add(f"connections:{process.pid}:{type(error).__name__}")
                continue
            for connection in connections:
                if not connection.raddr:
                    continue
                host = str(connection.raddr.ip)
                if host in {"127.0.0.1", "::1"}:
                    continue
                record = {
                    "pid": process.pid,
                    "remote_host": host,
                    "remote_port": int(connection.raddr.port),
                    "status": connection.status,
                }
                key = json.dumps(record, sort_keys=True)
                self.external[key] = record


class PaperAgentTargetAdapter:
    """PaperAgent composition over the generic native-process lifecycle."""

    def __init__(
        self,
        store: Store,
        config: PaperAgentCaseConfig,
        *,
        profile: NativeCommandProfile = PAPERAGENT_PROJECT_PROFILE,
        case: DeclarativeGradioCase = PAPERAGENT_INVALID_URL_CASE,
        verifier: PaperAgentInvalidUrlVerifier | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.profile = profile
        self.case = case
        self.verifier = verifier or PaperAgentInvalidUrlVerifier(case)
        self.runner = NativeCommandRunner(config.python_executable, profile)
        self.config.run_root.mkdir(parents=True, exist_ok=True)
        self.preflight: RuntimeEnvironmentPreflight | None = None
        self.resets: dict[tuple[str, int], dict[str, object]] = {}
        self.trials: dict[tuple[str, int], tuple[EvolutionTrial, EvolutionVerification]] = {}

    def tool_specs(self) -> list[dict[str, object]]:
        no_args = {"type": "object", "properties": {}, "additionalProperties": False}
        revision_args = {
            "type": "object",
            "properties": {
                "revision": {"type": "string", "enum": ["baseline", "candidate"]},
                "trial_index": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["revision", "trial_index"],
            "additionalProperties": False,
        }
        hypothesis = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "uncertainty": {"type": "string"},
            },
            "required": ["summary", "evidence_refs", "uncertainty"],
            "additionalProperties": False,
        }
        functions = (
            ("read_case_contracts", "Read approved PaperAgent case contracts and fixed revisions.", no_args),
            ("read_changeset_and_memory", "Read bounded ChangeSet and version-memory references.", no_args),
            ("request_preflight", "Run the deterministic seven-check environment preflight.", no_args),
            ("request_trial_reset", "Materialize one clean disposable revision runtime.", revision_args),
            ("request_trial_execution", "Execute the native Gradio event and independent verifier.", revision_args),
            ("read_trial_evidence", "Read bounded persisted evidence for one executed trial.", revision_args),
            ("submit_evaluation_hypothesis", "Submit a non-Gate evidence-linked pair hypothesis.", hypothesis),
            ("submit_insufficient_evidence", "Stop when infrastructure or evidence is insufficient.", hypothesis),
        )
        return [
            {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}
            for name, description, parameters in functions
        ]

    def execute(self, name: str, arguments: dict[str, object]) -> TargetObservation:
        if name == "read_case_contracts":
            self._require_no_args(arguments)
            return TargetObservation({
                "contract_refs": list(self.config.contract_refs),
                "baseline_commit": self.config.baseline.commit_sha,
                "candidate_commit": self.config.candidate.commit_sha,
                "request_fingerprint": _fingerprint({"api_name": self.case.api_name, "arguments": self.case.arguments}),
                "sut_native_model": "disabled",
            })
        if name == "read_changeset_and_memory":
            self._require_no_args(arguments)
            return TargetObservation({"evidence_refs": list(self.config.changeset_and_memory_refs)})
        if name == "request_preflight":
            self._require_no_args(arguments)
            preflight = self._run_preflight()
            return TargetObservation({
                "preflight_id": preflight.runtime_environment_preflight_id,
                "status": preflight.status,
                "environment_fingerprint": preflight.environment_fingerprint,
                "checks": [item.model_dump() for item in preflight.checks],
            })
        if name in {"request_trial_reset", "request_trial_execution", "read_trial_evidence"}:
            role, index = self._revision_arguments(arguments)
            key = (role, index)
            if name == "request_trial_reset":
                return TargetObservation(self._reset(role, index))
            if name == "request_trial_execution":
                return TargetObservation(self._trial_payload(*self._execute_trial(role, index)))
            if key not in self.trials:
                raise ValueError("Trial evidence is unavailable; execute the declared trial first")
            return TargetObservation(self._trial_payload(*self.trials[key]))
        if name in {"submit_evaluation_hypothesis", "submit_insufficient_evidence"}:
            return self._submit(name, arguments)
        raise ValueError(f"Unknown PaperAgent target tool: {name}")

    def restore(self, observations: list[dict[str, object]]) -> None:
        for payload in observations:
            if payload.get("preflight_id"):
                self.preflight = self.store.get("runtime_environment_preflight", str(payload["preflight_id"]), RuntimeEnvironmentPreflight)
            if payload.get("trial_id") and payload.get("verification_id"):
                trial = self.store.get("evolution_trial", str(payload["trial_id"]), EvolutionTrial)
                verification = self.store.get("evolution_verification", str(payload["verification_id"]), EvolutionVerification)
                if trial and verification:
                    self.trials[(trial.revision_role, trial.trial_index)] = (trial, verification)

    def _run_preflight(self) -> RuntimeEnvironmentPreflight:
        if self.preflight:
            return self.preflight
        checks: list[EnvironmentCheck] = []
        try:
            revisions = [self._revision_fact(self.config.baseline), self._revision_fact(self.config.candidate)]
            checks.append(EnvironmentCheck(
                name="docker", status="passed", evidence_ref=f"sha256:{_fingerprint(revisions)}",
                detail="Two pinned Git revisions are materialized into disposable local runtimes; no public deployment or container daemon is required.",
            ))
            lock_hash = _sha256(self.config.dependency_lock)
            wheels = sorted(self.config.wheelhouse.glob("*.whl"))
            if not wheels:
                raise TargetInfrastructureError("Hash-verified wheelhouse is empty")
            dependency = subprocess.run(
                [str(self.config.python_executable), "-m", "pip", "check"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            checks.append(EnvironmentCheck(
                name="dependency", status="passed", evidence_ref=f"sha256:{lock_hash}",
                detail=f"Hash-pinned offline environment contains {len(wheels)} wheels and pip check passed: {dependency}",
            ))
            binding = self.store.get("provider_binding", self.config.control_plane_binding_id, ProviderBinding)
            if not binding or binding.project_id != self.config.project_id or binding.role != "control_plane":
                raise TargetInfrastructureError("Approved project-scoped control-plane ProviderBinding is unavailable")
            checks.append(EnvironmentCheck(
                name="model_config", status="passed",
                evidence_ref=f"provider_binding:{binding.provider_binding_id};sut_native:disabled",
                detail="Control-plane binding remains outside the target; all target-native model and proxy variables are cleared.",
            ))
            for revision in (self.config.baseline, self.config.candidate):
                for relative in self.profile.required_source_files:
                    if not (revision.source / relative).is_file():
                        raise TargetInfrastructureError(f"Missing native entrypoint: {revision.role}/{relative}")
            checks.append(EnvironmentCheck(
                name="tools", status="passed", evidence_ref=f"sha256:{_sha256(PAPERAGENT_CLIENT_SCRIPT)}",
                detail="Unmodified PaperAgent Gradio application and external /summarize_file client adapter are present.",
            ))
            reset_a = self._materialize(self.config.baseline, self.config.run_root / "preflight" / "reset-a")
            reset_b = self._materialize(self.config.baseline, self.config.run_root / "preflight" / "reset-b")
            fingerprint_a = _tree_fingerprint(reset_a)
            fingerprint_b = _tree_fingerprint(reset_b)
            if fingerprint_a != fingerprint_b:
                raise TargetInfrastructureError("Two disposable source resets did not reproduce identical state")
            checks.append(EnvironmentCheck(
                name="reset", status="passed", evidence_ref=f"sha256:{fingerprint_a}",
                detail="Two independent git-archive resets reproduced the same complete file manifest.",
            ))
            payload = {"api_name": self.case.api_name, "arguments": self.case.arguments}
            checks.append(EnvironmentCheck(
                name="initial_state", status="passed", evidence_ref=f"sha256:{_fingerprint(payload)}",
                detail="Both revisions receive the identical neutral Link-mode event and an empty writable output root.",
            ))
            calibration = self.verifier.calibrate()
            expected = {"valid": "passed", "download_attempt": "failed", "partial_write": "failed", "missing_trace": "infrastructure_error"}
            if calibration != expected:
                raise TargetInfrastructureError(f"Verifier calibration failed: {calibration}")
            checks.append(EnvironmentCheck(
                name="verifier", status="passed", evidence_ref=f"sha256:{_fingerprint(calibration)}",
                detail="Verifier passed valid rejection, failed download/effect fixtures, and blocked missing trace.",
            ))
            environment_fingerprint = _fingerprint({
                "revisions": revisions,
                "lock": lock_hash,
                "wheels": {path.name: _sha256(path) for path in wheels},
                "python": str(self.config.python_executable.resolve()),
                "reset": fingerprint_a,
                "case": payload,
                "verifier": self.verifier.verifier_id,
                "profile": {
                    "profile_id": self.profile.profile_id,
                    "command_template": self.profile.command_template,
                    "required_source_files": self.profile.required_source_files,
                    "environment_templates": self.profile.environment_templates,
                    "cleared_secret_environment": self.profile.cleared_secret_environment,
                },
            })
            status = "passed"
        except (OSError, ValueError, subprocess.CalledProcessError, TargetInfrastructureError) as error:
            observed = {item.name for item in checks}
            for check_name in ("docker", "dependency", "model_config", "tools", "reset", "initial_state", "verifier"):
                if check_name not in observed:
                    checks.append(EnvironmentCheck(name=check_name, status="failed", detail=str(error)))
            environment_fingerprint = None
            status = "environment_not_satisfied"
        self.preflight = RuntimeEnvironmentPreflight(
            project_id=self.config.project_id,
            evolution_case_id=self.config.evolution_case_id,
            environment_contract_id=self.config.environment_contract_id,
            status=status,
            environment_fingerprint=environment_fingerprint,
            checks=checks,
        )
        self.store.save("runtime_environment_preflight", self.preflight.runtime_environment_preflight_id, self.config.project_id, self.preflight)
        return self.preflight

    def _reset(self, role: str, trial_index: int) -> dict[str, object]:
        preflight = self._run_preflight()
        if preflight.status != "passed" or not preflight.environment_fingerprint:
            raise TargetInfrastructureError("Environment preflight has not passed")
        revision = self._revision(role)
        trial_dir = self.config.run_root / "trials" / f"pair-{trial_index}" / role
        source = self._materialize(revision, trial_dir / "source")
        payload = {
            "revision": role,
            "trial_index": trial_index,
            "source": str(source),
            "source_fingerprint": _tree_fingerprint(source),
            "initial_state_fingerprint": _fingerprint({}),
            "environment_fingerprint": preflight.environment_fingerprint,
        }
        evidence_path = trial_dir / "reset.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["reset_evidence_ref"] = f"file:{evidence_path};sha256:{_sha256(evidence_path)}"
        self.resets[(role, trial_index)] = payload
        return payload

    def _execute_trial(self, role: str, trial_index: int) -> tuple[EvolutionTrial, EvolutionVerification]:
        key = (role, trial_index)
        if key in self.trials:
            return self.trials[key]
        if key not in self.resets:
            raise ValueError("Trial must be reset before execution")
        revision = self._revision(role)
        reset = self.resets[key]
        source = Path(str(reset["source"]))
        trial_dir = source.parent
        state_root = trial_dir / "state"
        (state_root / "user-profile").mkdir(parents=True, exist_ok=True)
        process, base_url, log_handle = self.runner.start_service(
            source=source,
            state_path=state_root,
            log_path=trial_dir / "service.log",
            readiness_path=PAPERAGENT_READINESS_PATH,
            label=f"paperagent-{role}-{trial_index}",
            startup_timeout_seconds=120,
        )
        lifecycle: list[dict[str, object]] = [{"operation": "readiness", "pid": process.pid, "base_url": base_url}]
        initial = _snapshot_trial_state(source, state_root, self.case.writable_root)
        monitor = _EffectMonitor(process.pid)
        monitor.start()
        started = time.monotonic()
        client_state = trial_dir / "client-state"
        client_work = trial_dir / "client-work"
        (client_state / "user-profile").mkdir(parents=True, exist_ok=True)
        client_work.mkdir(parents=True, exist_ok=True)
        try:
            request = {
                "api_name": self.case.api_name,
                "arguments": list(self.case.arguments),
                "output_names": list(self.case.output_names),
            }
            invocation = subprocess.run(
                [str(self.config.python_executable), str(PAPERAGENT_CLIENT_SCRIPT), base_url],
                cwd=client_work,
                env=self.runner.environment(source, client_state),
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                shell=False,
            )
            duration = time.monotonic() - started
            try:
                response = json.loads(invocation.stdout)
            except json.JSONDecodeError as error:
                raise TargetInfrastructureError("Native Gradio client did not return JSON") from error
            event_completed = invocation.returncode == 0
            lifecycle.append({"operation": "event", "exit_code": invocation.returncode, "duration_seconds": round(duration, 6)})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TargetInfrastructureError(f"Native Gradio event failed: {type(error).__name__}") from error
        finally:
            monitor.stop()
            self.runner.stop_service(process, log_handle)
            lifecycle.append({"operation": "termination", "exit_code": process.returncode})
        final = _snapshot_trial_state(source, state_root, self.case.writable_root)
        target_environment = self.runner.environment(source, state_root)
        cleared_environment = {
            name: bool(target_environment.get(name))
            for name in self.profile.cleared_secret_environment
        }
        model_environment_present = any(cleared_environment.values())
        evidence = {
            "revision_role": role,
            "revision_id": revision.revision_id,
            "commit_sha": revision.commit_sha,
            "request": {"api_name": self.case.api_name, "arguments": self.case.arguments},
            "request_fingerprint": _fingerprint({"api_name": self.case.api_name, "arguments": self.case.arguments}),
            "response": response,
            "client_stderr_sha256": hashlib.sha256(invocation.stderr.encode("utf-8")).hexdigest(),
            "event_completed": event_completed,
            "initial_files": initial,
            "final_files": final,
            "child_processes": list(monitor.children.values()),
            "external_connections": list(monitor.external.values()),
            "monitor_errors": sorted(monitor.errors),
            "target_native_model_environment_present": model_environment_present,
            "cleared_environment_presence": cleared_environment,
            "lifecycle": lifecycle,
            "source_fingerprint": reset["source_fingerprint"],
            "environment_fingerprint": reset["environment_fingerprint"],
            "reset_evidence_ref": reset["reset_evidence_ref"],
        }
        evidence_path = trial_dir / "trial-evidence.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence_ref = f"file:{evidence_path};sha256:{_sha256(evidence_path)}"
        verification_status, criteria = self.verifier.verify(PaperAgentEvidence(
            event_completed=event_completed,
            response=response,
            initial_files=initial,
            final_files=final,
            child_processes=tuple(monitor.children.values()),
            external_connections=tuple(monitor.external.values()),
            monitor_errors=tuple(sorted(monitor.errors)),
            model_environment_present=model_environment_present,
            lifecycle=tuple(lifecycle),
            source_fingerprint=str(reset["source_fingerprint"]),
            environment_fingerprint=str(reset["environment_fingerprint"]),
            request_fingerprint=str(evidence["request_fingerprint"]),
        ), evidence_ref)
        trial = EvolutionTrial(
            project_id=self.config.project_id,
            evolution_case_id=self.config.evolution_case_id,
            revision_id=revision.revision_id,
            revision_role=role,
            trial_index=trial_index,
            status="completed",
            environment_fingerprint=str(reset["environment_fingerprint"]),
            reset_evidence_ref=str(reset["reset_evidence_ref"]),
            request_fingerprint=str(evidence["request_fingerprint"]),
            response_evidence_ref=evidence_ref,
            trace_evidence_ref=evidence_ref,
            initial_state_ref=f"sha256:{_fingerprint(initial)}",
            final_state_ref=f"sha256:{_fingerprint(final)}",
            terminal_reason="native_gradio_completed",
        )
        verification = EvolutionVerification(
            project_id=self.config.project_id,
            evolution_case_id=self.config.evolution_case_id,
            evolution_trial_id=trial.evolution_trial_id,
            status=verification_status,
            criteria=criteria,
            evidence_refs=[evidence_ref],
        )
        self.store.save_many([
            ("evolution_trial", trial.evolution_trial_id, self.config.project_id, trial),
            ("evolution_verification", verification.evolution_verification_id, self.config.project_id, verification),
        ])
        self.trials[key] = (trial, verification)
        return trial, verification

    def _materialize(self, revision: PaperAgentRevision, destination: Path) -> Path:
        if destination.exists():
            if any(destination.iterdir()):
                raise TargetInfrastructureError(f"Disposable runtime already contains state: {destination}")
        else:
            destination.mkdir(parents=True)
        archive = destination.parent / f"{destination.name}.zip"
        if archive.exists():
            archive.unlink()
        subprocess.run(
            ["git", "-C", str(revision.source), "archive", "--format=zip", "--output", str(archive), revision.commit_sha],
            capture_output=True, text=True, check=True,
        )
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)
        archive.unlink()
        return destination

    def _submit(self, name: str, arguments: dict[str, object]) -> TargetObservation:
        summary = arguments.get("summary")
        uncertainty = arguments.get("uncertainty")
        evidence_refs = arguments.get("evidence_refs")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")
        if not isinstance(uncertainty, str) or not uncertainty.strip():
            raise ValueError("uncertainty must be a non-empty string")
        if not isinstance(evidence_refs, list) or not all(isinstance(item, str) for item in evidence_refs):
            raise ValueError("evidence_refs must be a list of strings")
        if name == "submit_evaluation_hypothesis":
            required = {verification.evolution_verification_id for _, verification in self.trials.values()}
            roles = {trial.revision_role for trial, _ in self.trials.values()}
            if roles != {"baseline", "candidate"} or not required <= set(evidence_refs):
                raise ValueError("A hypothesis must reference baseline and candidate verification IDs")
        kind = "hypothesis" if name == "submit_evaluation_hypothesis" else "insufficient_evidence"
        return TargetObservation({"hypothesis": {
            "kind": kind,
            "summary": summary.strip(),
            "evidence_refs": evidence_refs,
            "uncertainty": uncertainty.strip(),
        }}, terminal=True)

    @staticmethod
    def _trial_payload(trial: EvolutionTrial, verification: EvolutionVerification) -> dict[str, object]:
        return {
            "trial_id": trial.evolution_trial_id,
            "revision": trial.revision_role,
            "trial_index": trial.trial_index,
            "trial_status": trial.status,
            "verification_id": verification.evolution_verification_id,
            "verification_status": verification.status,
            "criteria": [item.model_dump() for item in verification.criteria],
            "evidence_refs": verification.evidence_refs,
        }

    def _revision(self, role: str) -> PaperAgentRevision:
        return self.config.baseline if role == "baseline" else self.config.candidate

    @staticmethod
    def _revision_fact(revision: PaperAgentRevision) -> dict[str, str]:
        observed = subprocess.run(
            ["git", "-C", str(revision.source), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if observed != revision.commit_sha:
            raise TargetInfrastructureError(f"{revision.role} source drift: expected {revision.commit_sha}, observed {observed}")
        status = subprocess.run(
            ["git", "-C", str(revision.source), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if status:
            raise TargetInfrastructureError(f"{revision.role} source worktree is not clean")
        return {"role": revision.role, "revision_id": revision.revision_id, "commit_sha": observed}

    @staticmethod
    def _require_no_args(arguments: dict[str, object]) -> None:
        if arguments:
            raise ValueError("This tool accepts no arguments")

    @staticmethod
    def _revision_arguments(arguments: dict[str, object]) -> tuple[str, int]:
        role = arguments.get("revision")
        index = arguments.get("trial_index")
        if role not in {"baseline", "candidate"} or not isinstance(index, int) or not 1 <= index <= 3:
            raise ValueError("revision must be baseline/candidate and trial_index must be 1..3")
        return str(role), index


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _snapshot_files(root: Path, relative_to: Path) -> dict[str, dict[str, object]]:
    if not root.exists():
        return {}
    return {
        path.relative_to(relative_to).as_posix(): {"size": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _snapshot_trial_state(
    source: Path, state_root: Path, writable_root: str
) -> dict[str, dict[str, object]]:
    output = _snapshot_files(source / writable_root, source)
    isolated = _snapshot_files(state_root, state_root)
    output.update({f"state/{path}": value for path, value in isolated.items()})
    return output


def _tree_fingerprint(root: Path) -> str:
    manifest = {
        path.relative_to(root).as_posix(): {"size": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    return _fingerprint(manifest)
