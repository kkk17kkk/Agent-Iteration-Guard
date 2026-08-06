"""Generic scenario input, fixture, and readiness contracts.

Scenario wording is not executable state.  This module makes the state needed
to exercise a generated scenario explicit and checks it before a target run.
Project adapters may materialize a fixture differently, but they must expose
the same contract and cannot silently substitute another input.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


FixtureAvailability = Literal["present", "absent"]
FixtureKind = Literal["file", "directory", "environment", "value"]
ReadinessCheckStatus = Literal["passed", "blocked"]
ScenarioReadinessStatus = Literal["ready", "blocked"]
ProviderUsageExpectation = Literal["required", "optional", "forbidden"]


class ScenarioInputRequirement(BaseModel):
    """One semantic input precondition declared by a generated scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_id: str = Field(min_length=1, max_length=100)
    fixture_id: str = Field(min_length=1, max_length=160)
    availability: FixtureAvailability
    description: str = Field(min_length=1, max_length=300)


class ScenarioTraceContract(BaseModel):
    """Scenario-specific trace expectations layered on the target contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_usage: ProviderUsageExpectation = "optional"
    required_event_types: list[str] = Field(default_factory=list, max_length=16)
    forbidden_event_types: list[str] = Field(default_factory=list, max_length=16)


class ScenarioInputContract(BaseModel):
    """The input state that must hold for one scenario to be meaningful."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.scenario-input-contract.v1"] = "aig.scenario-input-contract.v1"
    profile_id: str = Field(min_length=1, max_length=120)
    requirements: list[ScenarioInputRequirement] = Field(default_factory=list, max_length=16)
    trace: ScenarioTraceContract = Field(default_factory=ScenarioTraceContract)
    condition_traces: dict[str, ScenarioTraceContract] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def validate_condition_traces(self) -> "ScenarioInputContract":
        allowed = {"a_only", "b_only", "combined"}
        unknown = set(self.condition_traces).difference(allowed)
        if unknown:
            raise ValueError(f"Scenario condition_traces contain unsupported conditions: {sorted(unknown)}")
        return self

    def trace_for_condition(self, condition_kind: str) -> ScenarioTraceContract:
        """Resolve one arm's trace contract without changing the shared scenario input."""

        return self.condition_traces.get(condition_kind, self.trace)

    @classmethod
    def no_input(cls) -> "ScenarioInputContract":
        return cls(profile_id="no_input")


class FixtureDescriptor(BaseModel):
    """A project-declared fixture or a declared absence of one.

    ``source_ref`` is relative to the fixture root supplied by the project
    adapter.  The core never accepts an absolute path or a parent traversal.
    ``availability=absent`` can intentionally describe a missing input without
    creating a fake file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.fixture-descriptor.v1"] = "aig.fixture-descriptor.v1"
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    kind: FixtureKind
    availability: FixtureAvailability
    source_ref: str | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    purpose: str = Field(min_length=1, max_length=300)
    semantic_hints: dict[str, str] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def validate_source(self) -> "FixtureDescriptor":
        if self.availability == "present" and self.kind in {"file", "directory"} and not self.source_ref:
            raise ValueError("A present file or directory fixture requires source_ref.")
        if self.source_ref:
            path = Path(self.source_ref)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Fixture source_ref must stay within the configured fixture root.")
        if self.kind == "environment" and self.source_ref and not self.source_ref.replace("_", "").isalnum():
            raise ValueError("Environment fixture source_ref must be an environment variable name.")
        return self


class FixtureCatalog(BaseModel):
    """Project-owned fixture declarations persisted with Project Intelligence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.fixture-catalog.v1"] = "aig.fixture-catalog.v1"
    fixtures: list[FixtureDescriptor] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "FixtureCatalog":
        ids = [fixture.fixture_id for fixture in self.fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("Fixture Catalog cannot contain duplicate fixture_id values.")
        return self

    def get(self, fixture_id: str) -> FixtureDescriptor | None:
        return next((fixture for fixture in self.fixtures if fixture.fixture_id == fixture_id), None)


class MaterializedFixture(BaseModel):
    """Adapter output describing how a declared fixture reached a trial."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_id: str = Field(min_length=1)
    availability: FixtureAvailability
    reference: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)


class ScenarioFixtureProvider(Protocol):
    """Project adapter boundary for materializing declared fixtures."""

    def materialize(
        self,
        fixture: FixtureDescriptor,
        *,
        fixture_root: Path | None,
        trial_root: Path,
    ) -> MaterializedFixture: ...


class ScenarioReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    status: ReadinessCheckStatus
    detail: str = Field(min_length=1, max_length=500)
    fixture_id: str | None = None


class ScenarioReadinessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.scenario-readiness.v1"] = "aig.scenario-readiness.v1"
    scenario_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    status: ScenarioReadinessStatus
    checks: list[ScenarioReadinessCheck] = Field(min_length=1, max_length=32)
    blocking_reasons: list[str] = Field(default_factory=list, max_length=32)


class EvaluationReadinessResult(BaseModel):
    """Plan-level readiness result emitted before any target execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-readiness.v1"] = "aig.evaluation-readiness.v1"
    evaluation_plan_id: str = Field(min_length=1)
    status: ScenarioReadinessStatus
    scenarios: list[ScenarioReadinessResult] = Field(min_length=1, max_length=200)
    blocking_reasons: list[str] = Field(default_factory=list, max_length=200)


def check_scenario_readiness(
    *,
    scenario_id: str,
    category: str,
    input_contract: ScenarioInputContract,
    fixture_catalog: FixtureCatalog,
    fixture_root: Path | None = None,
) -> ScenarioReadinessResult:
    """Check one scenario without executing the target or inventing input."""

    checks: list[ScenarioReadinessCheck] = []
    blockers: list[str] = []

    def add(name: str, status: ReadinessCheckStatus, detail: str, fixture_id: str | None = None) -> None:
        checks.append(ScenarioReadinessCheck(name=name, status=status, detail=detail, fixture_id=fixture_id))
        if status == "blocked":
            blockers.append(detail)

    if category == "boundary" and not input_contract.requirements:
        add(
            "boundary_input_contract",
            "blocked",
            "Boundary scenario must declare the input state it is testing.",
        )
    elif not input_contract.requirements:
        add("input_contract", "passed", "Scenario declares that no external fixture is required.")

    for requirement in input_contract.requirements:
        fixture = fixture_catalog.get(requirement.fixture_id)
        if fixture is None:
            add(
                "fixture_declared",
                "blocked",
                f"Fixture {requirement.fixture_id!r} is not declared in the Project Fixture Catalog.",
                requirement.fixture_id,
            )
            continue
        if fixture.availability != requirement.availability:
            add(
                "fixture_availability",
                "blocked",
                f"Fixture {fixture.fixture_id!r} declares availability={fixture.availability}, "
                f"but the scenario requires availability={requirement.availability}.",
                fixture.fixture_id,
            )
            continue
        if fixture.availability == "absent":
            if fixture.source_ref and fixture_root is None:
                add(
                    "fixture_absence_verifiable",
                    "blocked",
                    f"Fixture {fixture.fixture_id!r} requires a fixture_root to verify its declared absence.",
                    fixture.fixture_id,
                )
            elif fixture.source_ref and _resolve_fixture_path(fixture_root, fixture.source_ref).exists():
                add(
                    "fixture_absence",
                    "blocked",
                    f"Fixture {fixture.fixture_id!r} is declared absent but the input exists.",
                    fixture.fixture_id,
                )
            else:
                add(
                    "fixture_absence",
                    "passed",
                    f"Fixture {fixture.fixture_id!r} is explicitly absent as required.",
                    fixture.fixture_id,
                )
            continue
        if fixture.kind in {"file", "directory"}:
            if fixture_root is None:
                add(
                    "fixture_root",
                    "blocked",
                    f"Fixture {fixture.fixture_id!r} requires a fixture_root before execution.",
                    fixture.fixture_id,
                )
                continue
            path = _resolve_fixture_path(fixture_root, fixture.source_ref)
            type_ok = path.is_file() if fixture.kind == "file" else path.is_dir()
            if not type_ok:
                add(
                    "fixture_materialized",
                    "blocked",
                    f"Fixture {fixture.fixture_id!r} is not materialized at {fixture.source_ref!r}.",
                    fixture.fixture_id,
                )
                continue
            if fixture.content_sha256 and fixture.kind == "file":
                observed = hashlib.sha256(path.read_bytes()).hexdigest()
                if observed != fixture.content_sha256:
                    add(
                        "fixture_integrity",
                        "blocked",
                        f"Fixture {fixture.fixture_id!r} content hash does not match its declaration.",
                        fixture.fixture_id,
                    )
                    continue
            add(
                "fixture_materialized",
                "passed",
                f"Fixture {fixture.fixture_id!r} is available and matches its declaration.",
                fixture.fixture_id,
            )
        else:
            add(
                "fixture_declared",
                "passed",
                f"Fixture {fixture.fixture_id!r} is declared for adapter-level materialization.",
                fixture.fixture_id,
            )

    return ScenarioReadinessResult(
        scenario_id=scenario_id,
        category=category,
        status="ready" if not blockers else "blocked",
        checks=checks or [ScenarioReadinessCheck(
            name="input_contract", status="passed", detail="Scenario input contract is valid."
        )],
        blocking_reasons=blockers,
    )


def check_evaluation_plan_readiness(
    plan,
    fixture_catalog: FixtureCatalog,
    *,
    fixture_root: Path | None = None,
) -> EvaluationReadinessResult:
    """Check every planned scenario before a target matrix is started."""

    results = [
        check_scenario_readiness(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            input_contract=scenario.input_contract,
            fixture_catalog=fixture_catalog,
            fixture_root=fixture_root,
        )
        for scenario in plan.scenarios
    ]
    blockers = [
        f"{item.scenario_id}: {reason}"
        for item in results
        for reason in item.blocking_reasons
    ]
    return EvaluationReadinessResult(
        evaluation_plan_id=plan.plan_id,
        status="ready" if not blockers else "blocked",
        scenarios=results,
        blocking_reasons=blockers,
    )


def verify_scenario_trace_contract(
    contract: ScenarioInputContract,
    events: list[dict[str, object]],
    *,
    condition_kind: str | None = None,
) -> list[str]:
    """Return deterministic trace contract violations for one scenario."""

    trace = contract.trace_for_condition(condition_kind) if condition_kind else contract.trace
    observed_types = {str(item.get("event_type") or "") for item in events}
    violations: list[str] = []
    missing = sorted(set(trace.required_event_types).difference(observed_types))
    forbidden = sorted(set(trace.forbidden_event_types).intersection(observed_types))
    if missing:
        violations.append("missing required scenario event types: " + ", ".join(missing))
    if forbidden:
        violations.append("observed forbidden scenario event types: " + ", ".join(forbidden))
    provider_events = [item for item in events if item.get("request_id")]
    if trace.provider_usage == "required" and not provider_events:
        violations.append("scenario requires provider usage but no provider event was observed")
    if trace.provider_usage == "forbidden" and provider_events:
        violations.append("scenario forbids provider usage but a provider event was observed")
    return violations


def _resolve_fixture_path(fixture_root: Path | None, source_ref: str | None) -> Path:
    if fixture_root is None or not source_ref:
        return Path()
    root = fixture_root.resolve()
    path = (root / source_ref).resolve()
    if path != root and root not in path.parents:
        raise ValueError("Fixture source_ref resolves outside the configured fixture_root.")
    return path


__all__ = [
    "EvaluationReadinessResult",
    "FixtureAvailability",
    "FixtureCatalog",
    "FixtureDescriptor",
    "FixtureKind",
    "MaterializedFixture",
    "ScenarioInputContract",
    "ScenarioInputRequirement",
    "ScenarioReadinessCheck",
    "ScenarioReadinessResult",
    "ScenarioFixtureProvider",
    "ScenarioTraceContract",
    "check_evaluation_plan_readiness",
    "check_scenario_readiness",
    "verify_scenario_trace_contract",
]
