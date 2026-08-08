"""Frozen, type-neutral Level 1 evidence contract.

This module is deliberately independent of Product Evaluation Analyst and
renderers.  Adapters write this contract; downstream semantic components may
read it but never change its persisted contents.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from .evolution_types import EvaluationType
EvidenceLevel = Literal["verified", "derived", "inferred", "unresolved"]
EvidenceRecordType = Literal[
    "trial_result",
    "verifier_result",
    "trace",
    "metric",
    "artifact",
    "declaration",
]


class EvidenceCondition(BaseModel):
    """One evaluator-defined experimental condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    condition_id: str = Field(min_length=1)
    # Present only when the execution artifact was produced for a planned
    # user scenario. Conditions without this field remain valid legacy
    # evidence, but cannot support a multi-scenario stability claim.
    scenario_id: str | None = Field(default=None, min_length=1)
    experiment_id: str | None = None
    label: str = Field(min_length=1)
    observations: dict[str, object] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(min_length=1)


class EvidenceFact(BaseModel):
    """A machine-derived fact retained with its source references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    fact_type: str = Field(min_length=1)
    value: dict[str, object] = Field(default_factory=dict)
    evidence_level: EvidenceLevel
    evidence_refs: list[str] = Field(min_length=1)


class EvidenceRecord(BaseModel):
    """Normalized representation of one persisted machine-level record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    record_type: EvidenceRecordType
    source_ref: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)
    evidence_level: EvidenceLevel = "verified"
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceMetric(BaseModel):
    """A replayable metric value, kept separate from semantic impact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value: int | float | str
    unit: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceIntegrity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["complete", "incomplete", "conflicted"]
    missing: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class ImmutableEvidenceBundle(BaseModel):
    """Common immutable envelope accepted from every Evaluation Adapter.

    ``type_data`` is intentionally opaque to the common layer.  Its keys are
    controlled by the adapter for ``evaluation_type`` and remain evaluator
    facts, never Analyst output.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evidence-bundle.v1"] = "aig.evidence-bundle.v1"
    evaluation_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    evaluation_name: str = Field(min_length=1)
    evaluation_type: EvaluationType
    evaluation_request_id: str | None = None
    baseline_version: str | None = None
    candidate_version: str | None = None
    scope_id: str | None = Field(default=None, min_length=16)
    evaluation_plan_id: str | None = None
    artifact_manifest_hash: str = Field(min_length=16)
    conditions: list[EvidenceCondition] = Field(min_length=1)
    facts: list[EvidenceFact] = Field(min_length=1)
    records: list[EvidenceRecord] = Field(default_factory=list)
    metrics: list[EvidenceMetric] = Field(default_factory=list)
    summary: dict[str, int | float | str] = Field(default_factory=dict)
    type_data: dict[str, object] = Field(default_factory=dict)
    integrity: EvidenceIntegrity


__all__ = [
    "EvidenceCondition",
    "EvidenceFact",
    "EvidenceIntegrity",
    "EvidenceLevel",
    "EvidenceMetric",
    "EvidenceRecord",
    "EvidenceRecordType",
    "EvaluationType",
    "ImmutableEvidenceBundle",
]
