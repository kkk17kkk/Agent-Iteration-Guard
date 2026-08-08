"""Immutable runtime scope for one Evaluation Plan and its evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain import ProviderBinding
from .project_intelligence import AgentSnapshot, ProjectIntelligence, RuntimeProfile
from .evaluation_request import EvaluationRequest


SideEffectPolicy = Literal["isolated_read", "isolated_write"]


class EvaluationScopeError(ValueError):
    """Raised when an immutable execution scope cannot be frozen."""


class EvaluationScope(BaseModel):
    """Non-secret execution identity shared by Plan, Run, Evidence and Report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-scope.v1"] = "aig.evaluation-scope.v1"
    scope_id: str = Field(min_length=16)
    project_id: str = Field(min_length=1)
    evaluation_request_id: str = Field(min_length=1)
    baseline_version: str = Field(min_length=1)
    candidate_version: str = Field(min_length=1)
    baseline_runtime_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_runtime_fingerprint: str = Field(min_length=64, max_length=64)
    provider_binding_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    provider_binding_fingerprint: str = Field(min_length=64, max_length=64)
    target_provider_binding_id: str | None = None
    target_provider: str | None = None
    target_model: str | None = None
    target_provider_binding_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    fixture_catalog_fingerprint: str = Field(min_length=64, max_length=64)
    planned_trial_count: int = Field(ge=1)
    budget_usd: float = Field(ge=0)
    timeout_seconds: int = Field(gt=0)
    side_effect_policy: SideEffectPolicy
    frozen_at: str = Field(min_length=1)


def freeze_evaluation_scope(
    request: EvaluationRequest,
    intelligence: ProjectIntelligence,
    binding: ProviderBinding,
    *,
    planned_trial_count: int,
    side_effect_policy: SideEffectPolicy = "isolated_read",
    target_binding: ProviderBinding | None = None,
) -> EvaluationScope:
    """Freeze the registered baseline/candidate runtime pair and constraints."""

    if request.project_id != intelligence.project_id or binding.project_id != request.project_id:
        raise EvaluationScopeError("Evaluation Scope inputs must belong to the same project.")
    if target_binding is not None:
        if target_binding.project_id != request.project_id or target_binding.role != "sut_native":
            raise EvaluationScopeError("Evaluation Scope target binding must be a sut_native binding in the same project.")
    baseline = _snapshot_for_version(intelligence, request.baseline_version)
    candidate = _snapshot_for_version(intelligence, request.candidate_version)
    if baseline is None or candidate is None:
        raise EvaluationScopeError(
            "Evaluation Scope requires registered baseline and candidate snapshots; runtime identity is unresolved."
        )
    if planned_trial_count < 1:
        raise EvaluationScopeError("Evaluation Scope requires at least one planned trial.")
    payload = {
        "project_id": request.project_id,
        "evaluation_request_id": request.request_id,
        "baseline_version": request.baseline_version,
        "candidate_version": request.candidate_version,
        "baseline_runtime_fingerprint": runtime_fingerprint(baseline.runtime_profile),
        "candidate_runtime_fingerprint": runtime_fingerprint(candidate.runtime_profile),
        "provider_binding_id": binding.provider_binding_id,
        "provider": binding.provider,
        "model": binding.model,
        "provider_binding_fingerprint": provider_binding_fingerprint(binding),
        "target_provider_binding_id": target_binding.provider_binding_id if target_binding else None,
        "target_provider": target_binding.provider if target_binding else None,
        "target_model": target_binding.model if target_binding else None,
        "target_provider_binding_fingerprint": (
            provider_binding_fingerprint(target_binding) if target_binding else None
        ),
        "fixture_catalog_fingerprint": fixture_catalog_fingerprint(candidate.runtime_profile),
        "planned_trial_count": planned_trial_count,
        "budget_usd": binding.batch_budget_usd,
        "timeout_seconds": binding.timeout_seconds,
        "side_effect_policy": side_effect_policy,
    }
    scope_id = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EvaluationScope(
        scope_id=scope_id,
        frozen_at=datetime.now(timezone.utc).isoformat(),
        **payload,
    )


def runtime_fingerprint(runtime: RuntimeProfile) -> str:
    return _sha256(runtime.model_dump(mode="json"))


def provider_binding_fingerprint(binding: ProviderBinding) -> str:
    payload = binding.model_dump(mode="json")
    payload.pop("created_at", None)
    return _sha256(payload)


def fixture_catalog_fingerprint(runtime: RuntimeProfile) -> str:
    return _sha256(runtime.fixture_catalog.model_dump(mode="json"))


def _snapshot_for_version(
    intelligence: ProjectIntelligence,
    version: str,
) -> AgentSnapshot | None:
    return next((item for item in intelligence.snapshot_history if item.version == version), None)


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "EvaluationScope",
    "EvaluationScopeError",
    "SideEffectPolicy",
    "fixture_catalog_fingerprint",
    "freeze_evaluation_scope",
    "provider_binding_fingerprint",
    "runtime_fingerprint",
]
