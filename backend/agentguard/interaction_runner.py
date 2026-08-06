"""Generic target protocol for scenario-aware Interaction Evaluation.

The matrix executor owns coverage.  This module owns the boundary between a
registered target and that executor:

* the target receives one JSON request on stdin;
* the target returns one JSON observation on stdout and emits its declared
  trace;
* an independent Oracle receives the observation in a separate process (or a
  separately implemented verifier) and records product outcome evidence.

No target names, skill names, prompts, or product rules are embedded here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import ProviderBinding
from .evaluation_planning import EvaluationScenario
from .interaction_matrix import ConditionKind, InteractionTrialResult
from .scenario_contracts import (
    FixtureCatalog,
    FixtureDescriptor,
    MaterializedFixture,
    ScenarioInputContract,
    ScenarioTraceContract,
    verify_scenario_trace_contract,
)
from .target_runtime import TargetRuntimeAdapter
from .targets import TargetInfrastructureError
from .integrations.native_command import CommandOperation


OracleOutcome = Literal["passed", "failed", "unresolved"]
OracleType = Literal["rule_based", "frozen_lookup", "structured_state"]
OracleAssertionStatus = Literal["passed", "failed", "unresolved"]


class InteractionRunnerError(ValueError):
    """Raised when a target or independent Oracle violates the protocol."""


class InteractionFixtureBinding(BaseModel):
    """Non-secret fixture metadata sent to the target on stdin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_id: str = Field(min_length=1, max_length=160)
    availability: Literal["present", "absent"]
    kind: Literal["file", "directory", "environment", "value"]
    reference: str | None = None


class InteractionRequest(BaseModel):
    """Stable request envelope consumed by every registered target runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.interaction-request.v1"] = "aig.interaction-request.v1"
    scenario_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    condition_kind: ConditionKind
    user_prompt: str = Field(min_length=1, max_length=600)
    input_contract: ScenarioInputContract
    fixtures: list[InteractionFixtureBinding] = Field(default_factory=list, max_length=16)


class TargetInteractionObservation(BaseModel):
    """Only target-observed data; it contains no Oracle verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.interaction-observation.v1"] = "aig.interaction-observation.v1"
    output: object
    trace: list[dict[str, object]] = Field(default_factory=list, max_length=10000)
    observations: dict[str, object] = Field(default_factory=dict)
    metrics: dict[str, int | float | str] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_metrics(self) -> "TargetInteractionObservation":
        for key in ("latency_ms", "cost_usd"):
            value = self.metrics.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"target observation requires finite non-negative {key}")
        for event in self.trace:
            if not isinstance(event.get("event_type"), str) or not str(event["event_type"]).strip():
                raise ValueError("target observation trace events require event_type")
        return self


class OracleAssertion(BaseModel):
    """One independently checked product assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    status: OracleAssertionStatus
    detail: str = Field(min_length=1, max_length=500)


class IndependentOracleResult(BaseModel):
    """Oracle completion and product outcome, kept distinct."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.independent-oracle-result.v1"] = "aig.independent-oracle-result.v1"
    verifier_id: str = Field(min_length=1, max_length=160)
    oracle_type: OracleType
    oracle_version: str = Field(min_length=1, max_length=80)
    validation_input: dict[str, object] = Field(min_length=1)
    status: Literal["verified"] = "verified"
    outcome: OracleOutcome
    assertions: list[OracleAssertion] = Field(min_length=1, max_length=64)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=600)


class InteractionOracle(Protocol):
    """Independent product verifier boundary."""

    verifier_id: str

    def verify(
        self,
        request: InteractionRequest,
        observation: TargetInteractionObservation,
        *,
        trial_root: Path,
    ) -> IndependentOracleResult: ...


class FilesystemScenarioFixtureProvider:
    """Materialize declared local fixtures without inventing missing input."""

    def materialize(
        self,
        fixture: FixtureDescriptor,
        *,
        fixture_root: Path | None,
        trial_root: Path,
    ) -> MaterializedFixture:
        if fixture.availability == "absent":
            if fixture.source_ref and fixture_root is not None:
                path = _fixture_path(fixture_root, fixture.source_ref)
                if path.exists():
                    raise InteractionRunnerError(
                        f"Fixture {fixture.fixture_id!r} is declared absent but exists at {fixture.source_ref!r}."
                    )
            return MaterializedFixture(fixture_id=fixture.fixture_id, availability="absent")

        if fixture.kind in {"file", "directory"}:
            if fixture_root is None or not fixture.source_ref:
                raise InteractionRunnerError(
                    f"Present filesystem fixture {fixture.fixture_id!r} requires fixture_root and source_ref."
                )
            path = _fixture_path(fixture_root, fixture.source_ref)
            expected = path.is_file() if fixture.kind == "file" else path.is_dir()
            if not expected:
                raise InteractionRunnerError(
                    f"Fixture {fixture.fixture_id!r} is not materialized as the declared {fixture.kind}."
                )
            if fixture.content_sha256 and fixture.kind == "file":
                observed = hashlib.sha256(path.read_bytes()).hexdigest()
                if observed != fixture.content_sha256:
                    raise InteractionRunnerError(f"Fixture {fixture.fixture_id!r} content hash does not match.")
            return MaterializedFixture(
                fixture_id=fixture.fixture_id,
                availability="present",
                reference=str(path),
            )

        if fixture.kind == "environment":
            if not fixture.source_ref:
                raise InteractionRunnerError(f"Environment fixture {fixture.fixture_id!r} requires source_ref.")
            value = os.getenv(fixture.source_ref)
            if value is None:
                raise InteractionRunnerError(
                    f"Environment fixture {fixture.fixture_id!r} is not available at runtime."
                )
            return MaterializedFixture(
                fixture_id=fixture.fixture_id,
                availability="present",
                reference=fixture.source_ref,
                environment={fixture.source_ref: value},
            )

        return MaterializedFixture(
            fixture_id=fixture.fixture_id,
            availability="present",
            reference=fixture.source_ref,
        )


class SubprocessInteractionOracle:
    """Run an evaluator-owned JSON Oracle outside the target runtime.

    The Oracle command gets a JSON envelope on stdin and no target provider
    credentials.  A non-zero exit, malformed JSON, or an unverified result is
    an infrastructure error rather than a failed Agent trial.
    """

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        verifier_id: str,
        oracle_type: OracleType = "rule_based",
        oracle_version: str = "1.0",
        timeout_seconds: float = 60,
        max_output_bytes: int = 1024 * 1024,
        working_directory: Path | None = None,
    ) -> None:
        if not command:
            raise ValueError("Independent Oracle command cannot be empty.")
        self.command = command
        self.verifier_id = verifier_id
        self.oracle_type = oracle_type
        self.oracle_version = oracle_version
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.working_directory = working_directory.resolve() if working_directory else None

    def verify(
        self,
        request: InteractionRequest,
        observation: TargetInteractionObservation,
        *,
        trial_root: Path,
    ) -> IndependentOracleResult:
        oracle_input = {
            "schema_version": "aig.independent-oracle-input.v1",
            "request": request.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
        }
        input_path = trial_root / "oracle-input.json"
        input_path.write_text(json.dumps(oracle_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        command = [
            part.format(
                scenario_id=request.scenario_id,
                condition_kind=request.condition_kind,
                trial_root=str(trial_root.resolve()),
                observation_path=str(input_path.resolve()),
            )
            for part in self.command
        ]
        environment = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC")
            if key in os.environ
        }
        environment["PYTHONUTF8"] = "1"
        try:
            result = subprocess.run(
                command,
                cwd=self.working_directory or trial_root,
                env=environment,
                input=json.dumps(oracle_input, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InteractionRunnerError(
                f"Independent Oracle {self.verifier_id} failed: {type(error).__name__}"
            ) from error
        if len(result.stdout.encode("utf-8")) > self.max_output_bytes:
            raise InteractionRunnerError("Independent Oracle output exceeded the approved limit.")
        if result.returncode != 0:
            raise InteractionRunnerError(
                f"Independent Oracle {self.verifier_id} returned exit code {result.returncode}."
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise InteractionRunnerError("Independent Oracle did not return JSON.") from error
        try:
            oracle = IndependentOracleResult.model_validate(payload)
        except ValueError as error:
            raise InteractionRunnerError("Independent Oracle returned an invalid result contract.") from error
        if oracle.verifier_id != self.verifier_id:
            raise InteractionRunnerError("Independent Oracle verifier_id does not match its binding.")
        if oracle.oracle_type != self.oracle_type or oracle.oracle_version != self.oracle_version:
            raise InteractionRunnerError("Independent Oracle type/version does not match its binding.")
        return oracle


class ManifestInteractionTrialRunner:
    """Adapt a manifest-declared target command to InteractionTrialRunner."""

    def __init__(
        self,
        target: TargetRuntimeAdapter,
        *,
        fixture_catalog: FixtureCatalog,
        fixture_root: Path | None,
        oracle: InteractionOracle,
        binding: ProviderBinding | None = None,
        fixture_provider: FilesystemScenarioFixtureProvider | None = None,
    ) -> None:
        if target.manifest.interaction is None:
            raise InteractionRunnerError("Target manifest does not declare an interaction command.")
        if isinstance(oracle, SubprocessInteractionOracle) and tuple(oracle.command) == tuple(target.manifest.interaction.command):
            raise InteractionRunnerError("Independent Oracle command must be separate from the target interaction command.")
        self.target = target
        self.fixture_catalog = fixture_catalog
        self.fixture_root = fixture_root.resolve() if fixture_root else None
        self.oracle = oracle
        self.binding = binding
        self.fixture_provider = fixture_provider or FilesystemScenarioFixtureProvider()

    def run(
        self,
        scenario: EvaluationScenario,
        condition_kind: ConditionKind,
        *,
        trial_root: Path,
    ) -> InteractionTrialResult:
        trial_root = trial_root.resolve()
        trial_root.mkdir(parents=True, exist_ok=True)
        interaction = self.target.manifest.interaction
        assert interaction is not None
        materialized = self._materialize_fixtures(scenario.input_contract, trial_root)
        request = InteractionRequest(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            condition_kind=condition_kind,
            user_prompt=scenario.user_prompt,
            input_contract=scenario.input_contract,
            fixtures=[
                InteractionFixtureBinding(
                    fixture_id=item.fixture_id,
                    availability=item.availability,
                    kind=self.fixture_catalog.get(item.fixture_id).kind if self.fixture_catalog.get(item.fixture_id) else "value",
                    reference=item.reference,
                )
                for item in materialized
            ],
        )
        request_path = trial_root / "interaction-request.json"
        request_path.write_text(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        trace_path = trial_root / "target-trace.jsonl" if self.target.manifest.trace else None
        trial_environment = {
            key: value
            for item in materialized
            for key, value in item.environment.items()
        }
        state_path = trial_root / "state"
        try:
            evidence = self.target.run_command(
                CommandOperation(name="interaction_trial", stdin_json=request.model_dump(mode="json")),
                state_path=state_path,
                binding=self.binding,
                trace_path=trace_path,
                trial_environment=trial_environment,
                command_template=tuple(interaction.command),
                command_variables={
                    "scenario_id": scenario.scenario_id,
                    "condition_kind": condition_kind,
                    "request_path": request_path,
                    "trace_path": trace_path or "",
                },
                timeout_seconds=interaction.timeout_seconds,
            )
        except TargetInfrastructureError as error:
            raise InteractionRunnerError(str(error)) from error
        if evidence.exit_code != interaction.required_exit_code:
            raise InteractionRunnerError(
                f"Target interaction command returned exit code {evidence.exit_code}; "
                f"expected {interaction.required_exit_code}."
            )
        try:
            observation = TargetInteractionObservation.model_validate(evidence.stdout)
        except ValueError as error:
            raise InteractionRunnerError("Target interaction command returned an invalid observation contract.") from error

        trace = observation.trace
        scenario_trace = scenario.input_contract.trace_for_condition(condition_kind)
        if self.target.manifest.trace:
            if trace_path is None:
                raise InteractionRunnerError("Target trace contract did not receive a trace path.")
            try:
                target_trace = self.target.read_trace(trace_path, scenario_trace=scenario_trace)
            except TargetInfrastructureError as error:
                raise InteractionRunnerError(str(error)) from error
            if target_trace.verification.get("status") != "passed":
                raise InteractionRunnerError("Target trace did not satisfy its declared contract.")
            trace = list(target_trace.events)
        violations = verify_scenario_trace_contract(
            scenario.input_contract,
            trace,
            condition_kind=condition_kind,
        )
        if violations:
            raise InteractionRunnerError("Scenario trace contract failed: " + "; ".join(violations))
        if not trace:
            raise InteractionRunnerError("Target interaction must provide a non-empty structured trace.")

        metrics = dict(observation.metrics)
        metrics["latency_ms"] = round(evidence.duration_seconds * 1000, 3)
        persisted_observation = observation.model_copy(update={"trace": trace, "metrics": metrics})
        observation_path = trial_root / "interaction-observation.json"
        observation_path.write_text(
            json.dumps(persisted_observation.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        oracle = self.oracle.verify(request, persisted_observation, trial_root=trial_root)
        oracle_path = trial_root / "independent-oracle-result.json"
        oracle_path.write_text(json.dumps(oracle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        refs = [
            _content_ref(request_path),
            _content_ref(observation_path),
            _content_ref(oracle_path),
        ]
        return InteractionTrialResult(
            scenario_id=scenario.scenario_id,
            condition_kind=condition_kind,
            category=scenario.category,
            label=f"{scenario.category} / {condition_kind}",
            observations=persisted_observation.observations,
            trace=trace,
            output=persisted_observation.output,
            metrics=metrics,
            oracle=oracle.model_dump(mode="json"),
            evidence_refs=refs,
        )

    def _materialize_fixtures(
        self,
        contract: ScenarioInputContract,
        trial_root: Path,
    ) -> list[MaterializedFixture]:
        result: list[MaterializedFixture] = []
        for requirement in contract.requirements:
            fixture = self.fixture_catalog.get(requirement.fixture_id)
            if fixture is None:
                raise InteractionRunnerError(
                    f"Scenario references undeclared fixture {requirement.fixture_id!r}."
                )
            if fixture.availability != requirement.availability:
                raise InteractionRunnerError(
                    f"Scenario fixture availability mismatch for {fixture.fixture_id!r}."
                )
            result.append(self.fixture_provider.materialize(
                fixture, fixture_root=self.fixture_root, trial_root=trial_root,
            ))
        return result


def _fixture_path(root: Path, source_ref: str) -> Path:
    root = root.resolve()
    path = (root / source_ref).resolve()
    if path != root and root not in path.parents:
        raise InteractionRunnerError("Fixture source_ref resolves outside fixture_root.")
    return path


def _content_ref(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "FilesystemScenarioFixtureProvider",
    "IndependentOracleResult",
    "InteractionFixtureBinding",
    "InteractionOracle",
    "InteractionRequest",
    "InteractionRunnerError",
    "ManifestInteractionTrialRunner",
    "OracleAssertion",
    "OracleType",
    "SubprocessInteractionOracle",
    "TargetInteractionObservation",
]
