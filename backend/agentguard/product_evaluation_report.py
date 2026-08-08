"""Portable product-facing report contract.

The report keeps immutable evidence and the evaluation plan available for
audit, while exposing a separate semantic surface for product consumers.
Renderers use the semantic surface for the main narrative and technical
objects only through the explicit evidence explorer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .evaluation_planning import EvaluationPlan
from .benchmark_evidence import BenchmarkEvidence
from .evidence_bundle import ImmutableEvidenceBundle
from .product_evaluation_analyst import (
    AnalystLimitation,
    EvaluationContext,
    EvidenceExplorer,
    ExecutiveSummary,
    ExperimentAnalysis,
    ExperimentOverview,
    ProductAnalystInput,
    ProductAnalystResult,
    ProductFinding,
    ProductImpactInterpretation,
    ProductOverview,
    ProductRecommendation,
    ProductSemanticAnalysis,
    InteractionAnalysis,
    ScenarioStability,
)
from .semantic_reporting import ProductDefinition
from .scenario_contracts import ScenarioInputContract


class ReportSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    component_type: str = Field(min_length=1)
    component_name: str = Field(min_length=1)


class ReportEvaluationOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(min_length=1)
    evaluation_name: str = Field(min_length=1)
    evaluation_type: str = Field(min_length=1)
    question: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    evidence_status: Literal["evidence_complete", "evidence_limited"]


class ReportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_manifest_hash: str = Field(min_length=16)
    evidence_schema_version: str = Field(min_length=1)
    analyst_schema_version: str = Field(min_length=1)
    analyst_provider: str = Field(min_length=1)
    analyst_model: str = Field(min_length=1)
    analyst_request_id: str = Field(min_length=1)
    interpretation_evidence_level: Literal["inferred"] = "inferred"


class ProductEvaluationReport(BaseModel):
    """Generic ProductEvaluationReport for Skill, Tool, Memory, Prompt, Release."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aig.product-evaluation-report.v4"] = "aig.product-evaluation-report.v4"
    report_id: str = Field(min_length=1)
    report_type: Literal["product_evaluation"] = "product_evaluation"
    evaluation_type: str = Field(min_length=1)
    status: Literal["completed", "blocked"]
    subject: ReportSubject
    product_context: ProductDefinition
    evaluation: ReportEvaluationOverview

    # Level 2: product semantics. These fields are the renderer/API default.
    product_overview: ProductOverview
    evaluation_context: EvaluationContext
    executive_summary: ExecutiveSummary
    experiment_overview: ExperimentOverview
    experiment_analysis: list[ExperimentAnalysis] = Field(min_length=1, max_length=8)
    scenario_stability: ScenarioStability
    interaction_analysis: InteractionAnalysis | None = None
    evidence_explorer: EvidenceExplorer
    findings: list[ProductFinding] = Field(min_length=1, max_length=5)
    business_impact: ProductImpactInterpretation
    recommendations: list[ProductRecommendation] = Field(min_length=1, max_length=3)
    limitations: list[AnalystLimitation] = Field(min_length=1, max_length=3)

    # Level 1 and planning provenance. They are never rewritten by the Analyst.
    evaluation_plan: EvaluationPlan | None = None
    evidence: ImmutableEvidenceBundle
    supplementary_evidence: list[BenchmarkEvidence] = Field(default_factory=list, max_length=16)
    provenance: ReportProvenance
    report_hash: str = Field(min_length=16)

    def recompute_report_hash(self) -> str:
        """Recompute the persisted report hash without trusting its field."""

        payload = self.model_dump(mode="json")
        payload.pop("report_hash", None)
        return hashlib.sha256(
            json.dumps(_canonical_report_payload(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def assemble_product_evaluation_report(
    analyst_input: ProductAnalystInput,
    analyst_result: ProductAnalystResult,
    *,
    supplementary_evidence: list[BenchmarkEvidence] | None = None,
) -> ProductEvaluationReport:
    """Bind semantic interpretation to immutable evidence without changing facts."""

    evidence = analyst_input.evidence
    if evidence.evaluation_type != analyst_input.evaluation_type:
        raise ValueError("Analyst input and evidence bundle evaluation types do not match.")
    if evidence.project_id != analyst_input.project_id:
        raise ValueError("Analyst input and evidence bundle project IDs do not match.")

    analysis = analyst_result.analysis
    report_without_hash = {
        "schema_version": "aig.product-evaluation-report.v4",
        "report_id": _opaque_id("report", evidence.artifact_manifest_hash),
        "report_type": "product_evaluation",
        "evaluation_type": analyst_input.evaluation_type,
        "status": "completed",
        "subject": {
            "product_id": analyst_input.project_id,
            "component_type": analyst_input.product_definition.component_type,
            "component_name": analyst_input.product_definition.component_name,
        },
        "product_context": analyst_input.product_definition.model_dump(mode="json"),
        "evaluation": {
            "evaluation_id": evidence.evaluation_id,
            "evaluation_name": analyst_input.evaluation_name,
            "evaluation_type": analyst_input.evaluation_type,
            "question": analyst_input.evaluation_question,
            "hypothesis": analyst_input.hypothesis,
            "evidence_status": "evidence_complete" if evidence.integrity.status == "complete" else "evidence_limited",
        },
        "product_overview": analysis.product_overview.model_dump(mode="json"),
        "evaluation_context": analysis.evaluation_context.model_dump(mode="json"),
        "executive_summary": analysis.executive_summary.model_dump(mode="json"),
        "experiment_overview": analysis.experiment_overview.model_dump(mode="json"),
        "experiment_analysis": [item.model_dump(mode="json") for item in analysis.experiment_analysis],
        "scenario_stability": analysis.scenario_stability.model_dump(mode="json"),
        "interaction_analysis": analysis.interaction_analysis.model_dump(mode="json") if analysis.interaction_analysis else None,
        "evidence_explorer": analysis.evidence_explorer.model_dump(mode="json"),
        "findings": [item.model_dump(mode="json") for item in analysis.findings],
        "business_impact": analysis.business_impact.model_dump(mode="json"),
        "recommendations": [item.model_dump(mode="json") for item in analysis.recommendations],
        "limitations": [item.model_dump(mode="json") for item in analysis.limitations],
        "evaluation_plan": analyst_input.evaluation_plan.model_dump(mode="json") if analyst_input.evaluation_plan else None,
        "evidence": evidence.model_dump(mode="json"),
        "supplementary_evidence": [
            item.model_dump(mode="json") for item in (supplementary_evidence or [])
        ],
        "provenance": {
            "evidence_manifest_hash": evidence.artifact_manifest_hash,
            "evidence_schema_version": evidence.schema_version,
            "analyst_schema_version": analyst_input.schema_version,
            "analyst_provider": analyst_result.provider,
            "analyst_model": analyst_result.model,
            "analyst_request_id": analyst_result.request_id,
            "interpretation_evidence_level": "inferred",
        },
    }
    report_hash = hashlib.sha256(
        json.dumps(_canonical_report_payload(report_without_hash), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ProductEvaluationReport(**report_without_hash, report_hash=report_hash)


def product_evaluation_report_api_payload(report: ProductEvaluationReport) -> dict[str, object]:
    return report.model_dump(mode="json")


def _opaque_id(prefix: str, raw_value: str) -> str:
    return prefix + "_" + hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:16]


def _canonical_report_payload(payload: dict[str, object]) -> dict[str, object]:
    """Keep implicit no-input defaults hash-compatible across schema migration."""

    implicit_input = ScenarioInputContract.no_input().model_dump(mode="json")

    def normalize(value: object, *, key: str | None = None) -> object:
        if key == "input_contract" and value == implicit_input:
            return None
        if key == "supplementary_evidence" and value == []:
            return None
        if isinstance(value, float) and value.is_integer():
            # Browser JSON serializers emit 0.0/1.0 as 0/1.  Canonicalize
            # equivalent JSON numbers before hashing the report contents.
            return int(value)
        if isinstance(value, dict):
            normalized: dict[str, object] = {}
            for item_key, item_value in value.items():
                child = normalize(item_value, key=item_key)
                if item_key == "input_contract" and child is None:
                    continue
                normalized[item_key] = child
            return normalized
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    result = normalize(payload)
    if not isinstance(result, dict):
        raise TypeError("Product Evaluation Report hash payload must be an object.")
    return result


__all__ = [
    "BenchmarkEvidence",
    "ProductEvaluationReport",
    "ReportEvaluationOverview",
    "ReportProvenance",
    "ReportSubject",
    "assemble_product_evaluation_report",
    "product_evaluation_report_api_payload",
]
