"""Deterministic runtime admission contracts for Project Intelligence snapshots.

Runtime comparability is deliberately separate from target-specific process
onboarding.  It answers whether two uploaded snapshots are a fair capability
comparison and records what was checked before an EvaluationRequest is saved.
It never executes the uploaded Agent.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .project_intelligence import AgentSnapshot, RuntimeProfile


RuntimeCheckStatus = Literal["passed", "failed", "unresolved"]
RuntimePreflightStatus = Literal["passed", "failed", "unresolved"]
RuntimeComparabilityStatus = Literal["comparable", "incompatible", "unresolved"]


class RuntimeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    status: RuntimeCheckStatus
    detail: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class RuntimePreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.runtime-preflight.v1"] = "aig.runtime-preflight.v1"
    project_id: str = Field(min_length=1)
    snapshot_id: str | None = None
    snapshot_version: str | None = None
    profile_id: str = Field(min_length=1)
    status: RuntimePreflightStatus
    checks: list[RuntimeCheck] = Field(min_length=1)
    result_fingerprint: str = Field(min_length=64, max_length=64)


class RuntimeComparabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.runtime-comparability.v1"] = "aig.runtime-comparability.v1"
    project_id: str = Field(min_length=1)
    baseline_snapshot_id: str = Field(min_length=1)
    baseline_version: str = Field(min_length=1)
    candidate_snapshot_id: str = Field(min_length=1)
    candidate_version: str = Field(min_length=1)
    status: RuntimeComparabilityStatus
    checks: list[RuntimeCheck] = Field(min_length=1)
    baseline_preflight: RuntimePreflightResult
    candidate_preflight: RuntimePreflightResult
    result_fingerprint: str = Field(min_length=64, max_length=64)


def preflight_runtime(
    profile: RuntimeProfile,
    *,
    snapshot_id: str | None = None,
    snapshot_version: str | None = None,
    source_root: Path | None = None,
) -> RuntimePreflightResult:
    """Check a declared runtime without starting it.

    A source root is optional because a registry may only contain an immutable
    repository/package/image reference.  When present, path-backed checks are
    stronger; when absent, the declaration remains visible as passed or
    unresolved instead of being replaced with a guessed local environment.
    """

    checks: list[RuntimeCheck] = []
    checks.append(_declared_check("entrypoint", bool(profile.entrypoint and profile.entrypoint.strip()), "entrypoint is declared"))
    checks.append(_declared_check("source_ref", bool(profile.source_ref.strip()), "source reference is declared"))
    checks.append(_declared_check(
        "execution_requirements",
        bool(profile.execution_requirements),
        "execution requirements are declared",
    ))
    checks.append(_declared_check(
        "model_configuration",
        all(str(value).strip() for value in profile.model_configuration.values()),
        "model configuration values are non-empty" if profile.model_configuration else "no model configuration is required",
    ))

    if profile.source_kind is not None and not profile.source_fingerprint:
        checks.append(RuntimeCheck(
            name="source_fingerprint",
            status="unresolved",
            detail="scanner-managed runtime has no immutable source fingerprint",
        ))
    else:
        checks.append(RuntimeCheck(
            name="source_fingerprint",
            status="passed",
            detail="source identity is pinned or the legacy profile does not declare a scanner source kind",
            evidence_refs=[profile.source_fingerprint] if profile.source_fingerprint else [],
        ))

    if profile.source_kind == "docker_image" and not (profile.image_digest or "@sha256:" in profile.source_ref):
        checks.append(RuntimeCheck(
            name="image_digest",
            status="unresolved",
            detail="Docker image reference is not pinned to a digest",
        ))
    elif profile.source_kind == "docker_image":
        checks.append(RuntimeCheck(
            name="image_digest",
            status="passed",
            detail="Docker image reference is digest-pinned",
            evidence_refs=[profile.image_digest or profile.source_ref],
        ))

    if source_root is not None:
        checks.extend(_source_path_checks(profile, source_root.resolve()))
    else:
        checks.append(RuntimeCheck(
            name="source_path",
            status="passed" if profile.source_fingerprint else ("unresolved" if profile.source_kind is not None else "passed"),
            detail=(
                "immutable source fingerprint is available; local path check is deferred"
                if profile.source_fingerprint
                else "local source root was not supplied for scanner-managed runtime"
                if profile.source_kind is not None
                else "local source root is outside the legacy registration contract"
            ),
        ))

    status = _aggregate_preflight_status(checks)
    fingerprint = _fingerprint({
        "project_id": profile.project_id,
        "profile_id": profile.profile_id,
        "snapshot_id": snapshot_id,
        "snapshot_version": snapshot_version,
        "status": status,
        "checks": [item.model_dump(mode="json") for item in checks],
    })
    return RuntimePreflightResult(
        project_id=profile.project_id,
        snapshot_id=snapshot_id,
        snapshot_version=snapshot_version,
        profile_id=profile.profile_id,
        status=status,
        checks=checks,
        result_fingerprint=fingerprint,
    )


def compare_runtime_snapshots(
    baseline: AgentSnapshot,
    candidate: AgentSnapshot,
    *,
    baseline_source_root: Path | None = None,
    candidate_source_root: Path | None = None,
) -> RuntimeComparabilityResult:
    """Return whether the two snapshot runtimes are a fair comparison."""

    if baseline.project_id != candidate.project_id:
        raise ValueError("Runtime comparison requires snapshots from the same project.")
    baseline_preflight = preflight_runtime(
        baseline.runtime_profile,
        snapshot_id=baseline.snapshot_id,
        snapshot_version=baseline.version,
        source_root=baseline_source_root,
    )
    candidate_preflight = preflight_runtime(
        candidate.runtime_profile,
        snapshot_id=candidate.snapshot_id,
        snapshot_version=candidate.version,
        source_root=candidate_source_root,
    )
    checks = [
        RuntimeCheck(
            name="baseline_preflight",
            status=baseline_preflight.status,
            detail=f"baseline runtime preflight is {baseline_preflight.status}",
            evidence_refs=[baseline_preflight.result_fingerprint],
        ),
        RuntimeCheck(
            name="candidate_preflight",
            status=candidate_preflight.status,
            detail=f"candidate runtime preflight is {candidate_preflight.status}",
            evidence_refs=[candidate_preflight.result_fingerprint],
        ),
    ]
    checks.extend(_compare_declared_runtime(baseline.runtime_profile, candidate.runtime_profile))
    status = _aggregate_comparability_status(checks)
    fingerprint = _fingerprint({
        "project_id": baseline.project_id,
        "baseline_snapshot_id": baseline.snapshot_id,
        "candidate_snapshot_id": candidate.snapshot_id,
        "status": status,
        "checks": [item.model_dump(mode="json") for item in checks],
        "baseline_preflight": baseline_preflight.result_fingerprint,
        "candidate_preflight": candidate_preflight.result_fingerprint,
    })
    return RuntimeComparabilityResult(
        project_id=baseline.project_id,
        baseline_snapshot_id=baseline.snapshot_id,
        baseline_version=baseline.version,
        candidate_snapshot_id=candidate.snapshot_id,
        candidate_version=candidate.version,
        status=status,
        checks=checks,
        baseline_preflight=baseline_preflight,
        candidate_preflight=candidate_preflight,
        result_fingerprint=fingerprint,
    )


def _compare_declared_runtime(baseline: RuntimeProfile, candidate: RuntimeProfile) -> list[RuntimeCheck]:
    checks: list[RuntimeCheck] = []
    checks.append(_equal_check("runtime_kind", baseline.runtime_kind, candidate.runtime_kind))
    checks.append(_equal_check("entrypoint", baseline.entrypoint, candidate.entrypoint))
    checks.append(_equal_check("model_configuration", baseline.model_configuration, candidate.model_configuration))
    checks.append(_equal_check("environment", baseline.environment, candidate.environment))
    checks.append(_equal_check("execution_requirements", baseline.execution_requirements, candidate.execution_requirements))
    checks.append(_equal_check("trace_contract_ref", baseline.trace_contract_ref, candidate.trace_contract_ref))
    checks.append(_equal_check("reset_contract_ref", baseline.reset_contract_ref, candidate.reset_contract_ref))
    checks.append(_equal_check(
        "fixture_catalog",
        baseline.fixture_catalog.model_dump(mode="json"),
        candidate.fixture_catalog.model_dump(mode="json"),
    ))

    checks.append(RuntimeCheck(
        name="source_kind",
        status="passed",
        detail="repository/package provenance may change; runtime contract is compared independently",
    ))
    if baseline.runtime_version != candidate.runtime_version:
        checks.append(RuntimeCheck(
            name="runtime_version",
            status="failed",
            detail="runtime version changed between baseline and candidate",
        ))
    else:
        checks.append(RuntimeCheck(name="runtime_version", status="passed", detail="runtime version is unchanged"))
    if baseline.dependencies != candidate.dependencies:
        checks.append(RuntimeCheck(
            name="dependencies",
            status="failed",
            detail="declared dependency contract changed; evaluate that runtime change separately",
        ))
    else:
        checks.append(RuntimeCheck(name="dependencies", status="passed", detail="declared dependencies are unchanged"))
    if baseline.dependency_lock_fingerprint != candidate.dependency_lock_fingerprint:
        checks.append(RuntimeCheck(
            name="dependency_lock_fingerprint",
            status="failed",
            detail="dependency lock fingerprint changed between baseline and candidate",
        ))
    else:
        checks.append(RuntimeCheck(name="dependency_lock_fingerprint", status="passed", detail="dependency lock is unchanged"))
    if baseline.image_digest != candidate.image_digest:
        checks.append(RuntimeCheck(
            name="image_digest",
            status="failed" if baseline.image_digest or candidate.image_digest else "passed",
            detail="container image digest changed" if baseline.image_digest or candidate.image_digest else "no image digest is declared",
        ))
    else:
        checks.append(RuntimeCheck(name="image_digest", status="passed", detail="container image digest is unchanged"))
    return checks


def _source_path_checks(profile: RuntimeProfile, source_root: Path) -> list[RuntimeCheck]:
    if not source_root.exists():
        return [RuntimeCheck(name="source_path", status="failed", detail=f"source root does not exist: {source_root}")]
    checks = [RuntimeCheck(name="source_path", status="passed", detail="source root exists")]
    for dependency in profile.dependencies:
        candidate = Path(dependency)
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        normalized = dependency.replace("\\", "/")
        is_declared_file = (
            "/" in normalized
            or normalized.lower().startswith(("requirements", "package-lock", "poetry.lock", "uv.lock"))
            or candidate.name.lower() in {"pyproject.toml", "package.json", "pnpm-lock.yaml", "yarn.lock"}
            or candidate.suffix.lower() in {".txt", ".lock", ".toml", ".json", ".yaml", ".yml"}
        )
        if is_declared_file:
            checks.append(RuntimeCheck(
                name=f"dependency:{dependency}",
                status="passed" if (source_root / candidate).is_file() else "failed",
                detail="declared dependency file exists" if (source_root / candidate).is_file() else "declared dependency file is missing",
            ))
    entrypoint_path = _entrypoint_path(profile.entrypoint)
    if entrypoint_path:
        resolved = (source_root / entrypoint_path).resolve()
        try:
            resolved.relative_to(source_root)
        except ValueError:
            checks.append(RuntimeCheck(name="entrypoint_path", status="failed", detail="entrypoint escapes the source root"))
        else:
            checks.append(RuntimeCheck(
                name="entrypoint_path",
                status="passed" if resolved.is_file() else "failed",
                detail="entrypoint file exists" if resolved.is_file() else "entrypoint file is missing",
            ))
    return checks


def _entrypoint_path(entrypoint: str | None) -> str | None:
    if not entrypoint:
        return None
    try:
        tokens = shlex.split(entrypoint, posix=False)
    except ValueError:
        tokens = entrypoint.split()
    for token in tokens:
        normalized = token.strip('"\'')
        if normalized.endswith(('.py', '.js', '.ts', '.mjs', '.cjs')):
            return normalized.replace("\\", "/")
    return None


def _declared_check(name: str, passed: bool, detail: str) -> RuntimeCheck:
    return RuntimeCheck(name=name, status="passed" if passed else "failed", detail=detail if passed else f"{name} is missing")


def _equal_check(name: str, baseline: object, candidate: object) -> RuntimeCheck:
    return RuntimeCheck(
        name=name,
        status="passed" if baseline == candidate else "failed",
        detail="declared contract is unchanged" if baseline == candidate else "declared contract changed",
    )


def _aggregate_preflight_status(checks: list[RuntimeCheck]) -> RuntimePreflightStatus:
    if any(item.status == "failed" for item in checks):
        return "failed"
    if any(item.status == "unresolved" for item in checks):
        return "unresolved"
    return "passed"


def _aggregate_comparability_status(checks: list[RuntimeCheck]) -> RuntimeComparabilityStatus:
    if any(item.status == "failed" for item in checks):
        return "incompatible"
    if any(item.status == "unresolved" for item in checks):
        return "unresolved"
    return "comparable"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "RuntimeCheck",
    "RuntimeCheckStatus",
    "RuntimeComparabilityResult",
    "RuntimeComparabilityStatus",
    "RuntimePreflightResult",
    "RuntimePreflightStatus",
    "compare_runtime_snapshots",
    "preflight_runtime",
]
