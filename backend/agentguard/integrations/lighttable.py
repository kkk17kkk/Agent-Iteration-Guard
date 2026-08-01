from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..domain import (
    EnvironmentCheck,
    EvolutionTrial,
    EvolutionVerification,
    ProviderBinding,
    RuntimeEnvironmentPreflight,
)
from ..store import Store
from ..targets import TargetInfrastructureError, TargetObservation
from .lighttable_case import LIGHTTABLE_CONSTRAINT_CASE, LightTableConstraintVerifier, verify_lighttable_trial
from .lighttable_profile import LIGHTTABLE_PROJECT_PROFILE
from .native_http import (
    DeclarativeHttpCase,
    NativeHttpEvidence,
    NativeHttpProcessRunner,
    NativeHttpProjectProfile,
    TrialVerifierPlugin,
    file_sha256 as _sha256,
    json_fingerprint as _json_fingerprint,
    request_json as _request_json,
    snapshot_sqlite_database as _snapshot_database,
)


@dataclass(frozen=True)
class LightTableRevision:
    role: str
    revision_id: str
    commit_sha: str
    source: Path


@dataclass(frozen=True)
class LightTableCaseConfig:
    project_id: str
    evolution_case_id: str
    environment_contract_id: str
    control_plane_binding_id: str
    run_root: Path
    python_executable: Path
    dependency_lock: Path
    wheelhouse: Path
    baseline: LightTableRevision
    candidate: LightTableRevision
    contract_refs: tuple[str, ...]
    changeset_and_memory_refs: tuple[str, ...] = ()


class LightTableTargetAdapter:
    """Bounded native-process Adapter for the approved LightTable constraint case."""

    def __init__(
        self,
        store: Store,
        config: LightTableCaseConfig,
        *,
        profile: NativeHttpProjectProfile = LIGHTTABLE_PROJECT_PROFILE,
        case: DeclarativeHttpCase = LIGHTTABLE_CONSTRAINT_CASE,
        verifier: TrialVerifierPlugin | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.profile = profile
        self.case = case
        self.verifier = verifier or LightTableConstraintVerifier()
        self.runner = NativeHttpProcessRunner(config.python_executable, profile)
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
        functions = [
            ("read_case_contracts", "Read the approved case contracts and fixed revisions.", no_args),
            ("read_changeset_and_memory", "Read the bounded ChangeSet and non-secret version-memory references.", no_args),
            ("request_preflight", "Run the deterministic seven-check environment preflight.", no_args),
            ("request_trial_reset", "Reset one declared revision/trial from the canonical seed.", revision_args),
            ("request_trial_execution", "Execute one reset revision/trial through native uvicorn HTTP and independent verification.", revision_args),
            ("read_trial_evidence", "Read the bounded persisted evidence for one executed trial.", revision_args),
            ("submit_evaluation_hypothesis", "Submit a non-Gate hypothesis linked to both revision verifications.", hypothesis),
            ("submit_insufficient_evidence", "Stop explicitly when infrastructure or evidence is insufficient.", hypothesis),
        ]
        return [{"type": "function", "function": {"name": name, "description": description, "parameters": parameters}} for name, description, parameters in functions]

    def execute(self, name: str, arguments: dict[str, object]) -> TargetObservation:
        if name == "read_case_contracts":
            self._require_no_args(arguments)
            return TargetObservation({
                "contract_refs": list(self.config.contract_refs),
                "baseline_commit": self.config.baseline.commit_sha,
                "candidate_commit": self.config.candidate.commit_sha,
                "request_fingerprint": _json_fingerprint(self.case.trial_operation.payload or {}),
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
            role, trial_index = self._revision_arguments(arguments)
            key = (role, trial_index)
            if name == "request_trial_reset":
                return TargetObservation(self._reset(role, trial_index))
            if name == "request_trial_execution":
                trial, verification = self._execute_trial(role, trial_index)
                return TargetObservation(self._trial_payload(trial, verification))
            if key not in self.trials:
                raise ValueError("Trial evidence is unavailable; execute the declared trial first")
            return TargetObservation(self._trial_payload(*self.trials[key]))
        if name in {"submit_evaluation_hypothesis", "submit_insufficient_evidence"}:
            return self._submit(name, arguments)
        raise ValueError(f"Unknown LightTable target tool: {name}")

    def restore(self, observations: list[dict[str, object]]) -> None:
        for payload in observations:
            if payload.get("preflight_id"):
                self.preflight = self.store.get(
                    "runtime_environment_preflight", str(payload["preflight_id"]), RuntimeEnvironmentPreflight
                )
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
            revision_facts = [self._revision_fact(self.config.baseline), self._revision_fact(self.config.candidate)]
            lock_hash = _sha256(self.config.dependency_lock)
            wheel_files = sorted(self.config.wheelhouse.glob("*.whl"))
            if not wheel_files:
                raise TargetInfrastructureError("Hash-verified wheelhouse is empty")
            dependency_check = subprocess.run(
                [str(self.config.python_executable), "-m", "pip", "check"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            checks.append(EnvironmentCheck(
                name="docker", status="passed",
                evidence_ref=f"sha256:{_json_fingerprint(revision_facts)}",
                detail="Two detached source runtimes and one isolated Python environment are pinned; no container daemon is required for this Windows pilot.",
            ))
            checks.append(EnvironmentCheck(
                name="dependency", status="passed", evidence_ref=f"sha256:{lock_hash}",
                detail=f"Hash-pinned lock, {len(wheel_files)} binary distributions, and isolated runtime passed: {dependency_check}",
            ))
            binding = self.store.get("provider_binding", self.config.control_plane_binding_id, ProviderBinding)
            if not binding or binding.project_id != self.config.project_id or binding.role != "control_plane":
                raise TargetInfrastructureError("Approved project-scoped control-plane ProviderBinding is unavailable")
            checks.append(EnvironmentCheck(
                name="model_config", status="passed", evidence_ref=f"provider_binding:{binding.provider_binding_id};sut_native:disabled",
                detail="Control-plane binding is separate; LightTable OPENROUTER and cloud Mem0 credentials are absent from the child environment.",
            ))
            if not self.case.catalog_relative_path:
                raise TargetInfrastructureError("LightTable case requires a frozen catalog")
            recipes = self.config.baseline.source / self.case.catalog_relative_path
            candidate_recipes = self.config.candidate.source / self.case.catalog_relative_path
            if _sha256(recipes) != _sha256(candidate_recipes):
                raise TargetInfrastructureError("Recipe catalogs differ between revisions")
            for revision in (self.config.baseline, self.config.candidate):
                for relative in self.profile.required_source_files:
                    if not (revision.source / relative).is_file():
                        raise TargetInfrastructureError(f"Missing native entrypoint: {revision.role}/{relative}")
            checks.append(EnvironmentCheck(
                name="tools", status="passed", evidence_ref=f"sha256:{_sha256(recipes)}",
                detail="Native backend.main:app, orchestrator, public HTTP operations, and identical frozen recipe catalogs are present.",
            ))
            seed = self._ensure_seed()
            first = self.config.run_root / "preflight" / "reset-1.db"
            second = self.config.run_root / "preflight" / "reset-2.db"
            first.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed, first)
            shutil.copy2(seed, second)
            if _sha256(first) != _sha256(second) or _snapshot_database(first) != _snapshot_database(second):
                raise TargetInfrastructureError("Two canonical-seed resets did not reproduce identical state")
            checks.append(EnvironmentCheck(
                name="reset", status="passed", evidence_ref=f"sha256:{_sha256(seed)}",
                detail="Two independent reset copies reproduced the same byte hash and SQLite rows.",
            ))
            initial = _snapshot_database(seed)
            user_rows = initial.get("user", [])
            inventory = initial.get("inventory_items", [])
            if len(user_rows) != 1 or len(inventory) != 3 or json.loads(str(user_rows[0].get("dislikes"))) != ["鸡蛋"]:
                raise TargetInfrastructureError("Canonical initial state does not match the approved case")
            checks.append(EnvironmentCheck(
                name="initial_state", status="passed", evidence_ref=f"sha256:{_json_fingerprint(initial)}",
                detail="Canonical state contains one default user, the approved dislike, three inventory rows, and no recommendation history.",
            ))
            calibration = self.verifier.calibrate(initial, json.loads(recipes.read_text(encoding="utf-8")))
            if calibration != {"valid": "passed", "wrong": "failed", "prohibited_write": "failed"}:
                raise TargetInfrastructureError(f"Verifier calibration failed: {calibration}")
            checks.append(EnvironmentCheck(
                name="verifier", status="passed", evidence_ref=f"sha256:{_json_fingerprint(calibration)}",
                detail="Independent verifier passed valid and rejected constraint-violating and prohibited-write fixtures.",
            ))
            fingerprint = _json_fingerprint({
                "revisions": revision_facts,
                "lock": lock_hash,
                "wheels": {path.name: _sha256(path) for path in wheel_files},
                "seed": _sha256(seed),
                "recipes": _sha256(recipes),
            })
            status = "passed"
        except (OSError, ValueError, subprocess.CalledProcessError, TargetInfrastructureError) as error:
            observed = {item.name for item in checks}
            for name in ("docker", "dependency", "model_config", "tools", "reset", "initial_state", "verifier"):
                if name not in observed:
                    checks.append(EnvironmentCheck(name=name, status="failed", detail=str(error)))
            fingerprint = None
            status = "environment_not_satisfied"
        self.preflight = RuntimeEnvironmentPreflight(
            project_id=self.config.project_id,
            evolution_case_id=self.config.evolution_case_id,
            environment_contract_id=self.config.environment_contract_id,
            status=status,
            environment_fingerprint=fingerprint,
            checks=checks,
        )
        self.store.save(
            "runtime_environment_preflight", self.preflight.runtime_environment_preflight_id,
            self.config.project_id, self.preflight,
        )
        return self.preflight

    def _ensure_seed(self) -> Path:
        seed = self.config.run_root / "canonical-seed.db"
        if seed.exists():
            return seed
        seed.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.run_root / "canonical-seed.building.db"
        if temporary.exists():
            temporary.unlink()
        process, base_url, log_handle = self._start_process(self.config.baseline, temporary, "seed-build")
        try:
            statuses = [self.runner.execute(base_url, operation)[0] for operation in self.case.setup_operations]
            if any(status != 200 for status in statuses):
                raise TargetInfrastructureError(f"Seed setup failed: statuses={statuses}")
        finally:
            self._stop_process(process, log_handle)
        temporary.replace(seed)
        return seed

    def _reset(self, role: str, trial_index: int) -> dict[str, object]:
        preflight = self._run_preflight()
        if preflight.status != "passed" or not preflight.environment_fingerprint:
            raise TargetInfrastructureError("Environment preflight has not passed")
        seed = self._ensure_seed()
        trial_dir = self.config.run_root / "trials" / f"pair-{trial_index}" / role
        trial_dir.mkdir(parents=True, exist_ok=True)
        database = trial_dir / "lighttable.db"
        shutil.copy2(seed, database)
        payload: dict[str, object] = {
            "revision": role,
            "trial_index": trial_index,
            "database": str(database),
            "seed_sha256": _sha256(seed),
            "database_sha256": _sha256(database),
            "initial_state_fingerprint": _json_fingerprint(_snapshot_database(database)),
            "environment_fingerprint": preflight.environment_fingerprint,
        }
        evidence = trial_dir / "reset.json"
        evidence.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["reset_evidence_ref"] = f"file:{evidence};sha256:{_sha256(evidence)}"
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
        database = Path(str(reset["database"]))
        initial = _snapshot_database(database)
        trial_dir = database.parent
        process, base_url, log_handle = self._start_process(revision, database, f"trial-{trial_index}-{role}")
        started = time.monotonic()
        trace: list[dict[str, object]] = []
        try:
            status_status, status_body = _request_json(f"{base_url}{self.profile.readiness_path}")
            trace.append({"operation": "readiness", "status": status_status, "body": status_body})
            response_status, response = self.runner.execute(base_url, self.case.trial_operation)
            trace.append({"operation": "recommend", "status": response_status, "elapsed_ms": round((time.monotonic() - started) * 1000, 3)})
        except (OSError, ValueError) as error:
            raise TargetInfrastructureError(f"Native trial HTTP failed: {error}") from error
        finally:
            self._stop_process(process, log_handle)
        final = _snapshot_database(database)
        if not self.case.catalog_relative_path:
            raise TargetInfrastructureError("LightTable case requires a frozen catalog")
        recipes_path = revision.source / self.case.catalog_relative_path
        recipes = json.loads(recipes_path.read_text(encoding="utf-8"))
        evidence = {
            "revision_role": role,
            "revision_id": revision.revision_id,
            "commit_sha": revision.commit_sha,
            "request": self.case.trial_operation.payload,
            "request_fingerprint": _json_fingerprint(self.case.trial_operation.payload or {}),
            "response_status": response_status,
            "response": response,
            "trace": trace,
            "initial": initial,
            "final": final,
            "initial_fingerprint": _json_fingerprint(initial),
            "final_fingerprint": _json_fingerprint(final),
            "recipes_sha256": _sha256(recipes_path),
            "reset_evidence_ref": reset["reset_evidence_ref"],
        }
        evidence_path = trial_dir / "trial-evidence.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence_ref = f"file:{evidence_path};sha256:{_sha256(evidence_path)}"
        verification_status, criteria = self.verifier.verify(
            NativeHttpEvidence(response_status, response, initial, final, trace, recipes), evidence_ref
        )
        trial = EvolutionTrial(
            project_id=self.config.project_id,
            evolution_case_id=self.config.evolution_case_id,
            revision_id=revision.revision_id,
            revision_role=role,
            trial_index=trial_index,
            status="completed",
            environment_fingerprint=str(reset["environment_fingerprint"]),
            reset_evidence_ref=str(reset["reset_evidence_ref"]),
            request_fingerprint=_json_fingerprint(self.case.trial_operation.payload or {}),
            response_evidence_ref=evidence_ref,
            trace_evidence_ref=evidence_ref,
            initial_state_ref=f"sha256:{evidence['initial_fingerprint']}",
            final_state_ref=f"sha256:{evidence['final_fingerprint']}",
            terminal_reason="native_http_completed",
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

    def _start_process(
        self, revision: LightTableRevision, database: Path, label: str
    ) -> tuple[subprocess.Popen[bytes], str, object]:
        log_path = database.parent / f"{label}.log"
        return self.runner.start(source=revision.source, state_path=database, log_path=log_path, label=label)

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes], log_handle: object) -> None:
        NativeHttpProcessRunner.stop(process, log_handle)

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
            required = {
                verification.evolution_verification_id
                for _, verification in self.trials.values()
                if verification.evolution_trial_id
            }
            roles = {trial.revision_role for trial, _ in self.trials.values()}
            if roles != {"baseline", "candidate"} or not required <= set(evidence_refs):
                raise ValueError("A hypothesis must reference executed baseline and candidate verification IDs")
        kind = "hypothesis" if name == "submit_evaluation_hypothesis" else "insufficient_evidence"
        return TargetObservation({
            "hypothesis": {
                "kind": kind,
                "summary": summary.strip(),
                "evidence_refs": evidence_refs,
                "uncertainty": uncertainty.strip(),
            }
        }, terminal=True)

    def _trial_payload(self, trial: EvolutionTrial, verification: EvolutionVerification) -> dict[str, object]:
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

    def _revision(self, role: str) -> LightTableRevision:
        return self.config.baseline if role == "baseline" else self.config.candidate

    @staticmethod
    def _revision_fact(revision: LightTableRevision) -> dict[str, str]:
        result = subprocess.run(
            ["git", "-C", str(revision.source), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if result != revision.commit_sha:
            raise TargetInfrastructureError(f"{revision.role} source drift: expected {revision.commit_sha}, observed {result}")
        return {"role": revision.role, "revision_id": revision.revision_id, "commit_sha": result}

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
