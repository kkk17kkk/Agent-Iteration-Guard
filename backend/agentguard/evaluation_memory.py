"""Evidence-backed Evaluation Knowledge for the Project Intelligence Layer.

Evaluation Knowledge is a planning aid, not a verdict store.  It records
patterns observed in completed evaluations and keeps every recommendation
linked to a source evaluation and evidence reference before it can be reused
by a Planner or Scenario Generator.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .store import Store


KnowledgeEvidenceLevel = Literal["observed", "inferred", "mixed"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class EvaluationKnowledge(BaseModel):
    """Reusable testing knowledge with explicit provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-knowledge.v1"] = "aig.evaluation-knowledge.v1"
    knowledge_id: str = Field(default="", min_length=0)
    project_id: str = Field(min_length=1)
    component_pattern: str = Field(min_length=1, max_length=160)
    common_risks: list[str] = Field(default_factory=list, max_length=12)
    recommended_dimensions: list[str] = Field(default_factory=list, max_length=12)
    scenario_templates: list[str] = Field(default_factory=list, max_length=12)
    source_evaluation_ids: list[str] = Field(min_length=1, max_length=32)
    evidence_refs: list[str] = Field(min_length=1, max_length=32)
    evidence_level: KnowledgeEvidenceLevel = "observed"
    sample_count: int = Field(default=1, ge=1)
    updated_at: str = Field(default_factory=_now)
    knowledge_fingerprint: str = Field(default="", min_length=0)


class EvaluationKnowledgeRepository:
    """Persist immutable-by-value, idempotently merged knowledge records."""

    _KIND = "evaluation_knowledge"

    def __init__(self, store: Store) -> None:
        self.store = store

    def record(self, knowledge: EvaluationKnowledge) -> EvaluationKnowledge:
        if not knowledge.source_evaluation_ids:
            raise ValueError("Evaluation Knowledge requires a source evaluation id.")
        if not knowledge.evidence_refs:
            raise ValueError("Evaluation Knowledge requires at least one evidence reference.")
        record_id = knowledge.knowledge_id or self._stable_id(
            knowledge.project_id, knowledge.component_pattern
        )
        existing = self.store.get(self._KIND, record_id, EvaluationKnowledge)
        if existing is not None and existing.project_id != knowledge.project_id:
            raise ValueError("Evaluation Knowledge record belongs to another project.")

        if existing is None:
            merged = self._normalized(knowledge, record_id)
        else:
            new_source_ids = sorted(set(knowledge.source_evaluation_ids) - set(existing.source_evaluation_ids))
            if not new_source_ids and self._semantic(existing) == self._semantic(knowledge):
                return existing
            merged = self._normalized(
                knowledge,
                record_id,
                source_evaluation_ids=sorted(set(existing.source_evaluation_ids) | set(knowledge.source_evaluation_ids)),
                evidence_refs=sorted(set(existing.evidence_refs) | set(knowledge.evidence_refs)),
                common_risks=_merge_labels(existing.common_risks, knowledge.common_risks),
                recommended_dimensions=_merge_labels(existing.recommended_dimensions, knowledge.recommended_dimensions),
                scenario_templates=_merge_labels(existing.scenario_templates, knowledge.scenario_templates),
                evidence_level=_merge_evidence_level(existing.evidence_level, knowledge.evidence_level),
                sample_count=existing.sample_count + (knowledge.sample_count if new_source_ids else 0),
            )
        self.store.save(self._KIND, record_id, merged.project_id, merged)
        return merged

    def list(self, project_id: str, component_pattern: str | None = None) -> list[EvaluationKnowledge]:
        records = self.store.list(self._KIND, EvaluationKnowledge, project_id)
        if component_pattern:
            records = [item for item in records if item.component_pattern == component_pattern]
        return sorted(records, key=lambda item: item.component_pattern)

    def _normalized(self, knowledge: EvaluationKnowledge, record_id: str, **updates: object) -> EvaluationKnowledge:
        payload = knowledge.model_copy(update={"knowledge_id": record_id, **updates})
        fingerprint = _fingerprint(self._semantic(payload))
        return payload.model_copy(update={"knowledge_fingerprint": fingerprint})

    @staticmethod
    def _stable_id(project_id: str, component_pattern: str) -> str:
        raw = f"{project_id}:{component_pattern}".encode("utf-8")
        return "knowledge_" + hashlib.sha256(raw).hexdigest()[:16]

    @staticmethod
    def _semantic(knowledge: EvaluationKnowledge) -> dict[str, object]:
        payload = knowledge.model_dump(mode="json")
        for key in ("knowledge_id", "updated_at", "knowledge_fingerprint"):
            payload.pop(key, None)
        return payload


def knowledge_from_report(report: BaseModel, *, component_pattern: str) -> EvaluationKnowledge:
    """Create a bounded, explicitly inferred observation from a report.

    The report's Analyst prose is never converted into an observed fact.  This
    helper labels the result ``inferred`` and retains the report/evidence refs
    so the next plan can treat it as a coverage hint only.
    """

    evaluation = getattr(report, "evaluation", None)
    evidence = getattr(report, "evidence", None)
    plan = getattr(report, "evaluation_plan", None)
    source_id = str(getattr(evaluation, "evaluation_id", ""))
    evidence_refs = {str(getattr(evidence, "artifact_manifest_hash", ""))}
    if not source_id or not evidence_refs or "" in evidence_refs:
        raise ValueError("Report must contain an evaluation id and evidence manifest hash.")

    dimensions: list[str] = []
    scenarios: list[str] = []
    if plan is not None:
        dimensions = [str(item.dimension) for item in plan.dimensions]
        scenarios = [str(item.category) for item in plan.scenarios]

    risks: list[str] = []
    for finding in getattr(report, "findings", []) or []:
        if getattr(finding, "finding_type", None) == "product_risk":
            risks.append(str(getattr(finding, "impact_dimension", "")))
            evidence_refs.update(str(ref) for ref in getattr(finding, "evidence_refs", []))
    risks = [item for item in dict.fromkeys(risks) if item]
    return EvaluationKnowledge(
        project_id=str(getattr(report.subject, "product_id")),
        component_pattern=component_pattern,
        common_risks=risks,
        recommended_dimensions=list(dict.fromkeys(dimensions)),
        scenario_templates=list(dict.fromkeys(scenarios)),
        source_evaluation_ids=[source_id],
        evidence_refs=sorted(ref for ref in evidence_refs if ref),
        evidence_level="inferred",
        sample_count=1,
    )


def _merge_labels(first: list[str], second: list[str]) -> list[str]:
    return list(dict.fromkeys([*first, *second]))


def _merge_evidence_level(
    first: KnowledgeEvidenceLevel, second: KnowledgeEvidenceLevel
) -> KnowledgeEvidenceLevel:
    return first if first == second else "mixed"


__all__ = [
    "EvaluationKnowledge",
    "EvaluationKnowledgeRepository",
    "KnowledgeEvidenceLevel",
    "knowledge_from_report",
]
