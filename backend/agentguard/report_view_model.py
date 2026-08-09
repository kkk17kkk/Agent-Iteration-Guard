"""Shared report presentation data for the application Preview and HTML export."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .product_evaluation_report import ProductEvaluationReport
from .project_intelligence import ProjectIntelligence
from .release_decision_gate import evaluate_release_decision


class ReportProjectContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    baseline: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    runtime: str = Field(min_length=1)


class NormalizedReport(BaseModel):
    """Stable report view model shared by API consumers and document renderers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    project: ReportProjectContext
    skill: dict[str, Any]
    versions: dict[str, str]
    runtime: dict[str, Any]
    decision: dict[str, Any]
    summary: dict[str, Any]
    metrics: dict[str, Any]
    capability_overview: dict[str, Any]
    evaluation_context: dict[str, Any]
    executive_summary: dict[str, Any]
    dimensions: list[dict[str, Any]]
    experiments: dict[str, Any]
    scenario_stability: dict[str, Any]
    impact: dict[str, Any]
    recommendations: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    evidence_bundle: dict[str, Any]
    technical_evidence: dict[str, Any]
    technical_metadata: dict[str, Any]


def project_context_from_intelligence(intelligence: ProjectIntelligence) -> dict[str, str]:
    """Project context is display metadata, never report content or evidence."""

    name = _display_name(intelligence.agent_manifest.agent_name, intelligence.project_id)
    return {
        "project_id": intelligence.project_id,
        "project_name": name,
        "purpose": f"当前已加载 {name} 项目，可查看版本、能力变化与评估结果。",
        "baseline": intelligence.baseline_snapshot.baseline_version,
        "candidate": intelligence.latest_snapshot.version if intelligence.latest_snapshot else intelligence.baseline_snapshot.baseline_version,
        "runtime": intelligence.runtime_profile.runtime_kind,
    }


def normalize_product_evaluation_report(
    report: ProductEvaluationReport,
    *,
    project_context: Mapping[str, object] | None = None,
    gate: Mapping[str, object] | None = None,
) -> NormalizedReport:
    """Project raw report data into the single stable presentation contract."""

    raw_evidence = report.evidence.model_dump(mode="json")
    evidence_summary = raw_evidence.get("summary") or {}
    conditions = [_normalize_condition(condition, raw_evidence.get("records") or []) for condition in raw_evidence.get("conditions") or []]
    condition_counts = {
        "verified": sum(item["status"] in {"passed", "failed"} for item in conditions),
        "passed": sum(item["status"] == "passed" for item in conditions),
        "failed": sum(item["status"] == "failed" for item in conditions),
        "review": sum(item["status"] == "review" for item in conditions),
    }
    cost = evidence_summary.get("total_cost_usd")
    if cost is None:
        observed_costs = [
            item.get("observations", {}).get("cost_usd")
            for item in conditions
            if isinstance(item.get("observations", {}).get("cost_usd"), (int, float))
        ]
        cost = sum(observed_costs) if observed_costs else None

    project = _project_context(report, project_context)
    gate_value = dict(gate or evaluate_release_decision(report))
    decision = {
        "decision": str(gate_value.get("decision") or "pending"),
        "rationale": str(gate_value.get("rationale") or "报告已加载，但尚未经过确定性 Gate 评估。"),
        "checks": [_dump_item(item) for item in gate_value.get("checks") or []],
    }
    evidence_status = report.evaluation.evidence_status
    experiment_count = len(report.experiment_analysis)
    findings_count = len(report.findings)
    metrics = {
        "report_status": report.status,
        "evidence_status": evidence_status,
        "experiment_count": experiment_count,
        "findings_count": findings_count,
        "verified_count": condition_counts["verified"],
        "passed_count": condition_counts["passed"],
        "failed_count": condition_counts["failed"],
        "review_count": condition_counts["review"],
        "condition_count": len(conditions),
        "cost_usd": cost,
    }
    report_payload = report.model_dump(mode="json")
    return NormalizedReport(
        report_id=report.report_id,
        title=f"{report.subject.component_name} 评估",
        project=project,
        skill={
            "name": report.subject.component_name,
            "type": report.subject.component_type,
            "product_id": report.subject.product_id,
        },
        versions={"baseline": project.baseline, "candidate": project.candidate},
        runtime={"kind": project.runtime},
        decision=decision,
        summary={
            "status": report.executive_summary.status,
            "final_conclusion": report.executive_summary.final_conclusion,
            "product_recommendation": report.executive_summary.product_recommendation,
            "follow_up_priorities": report.executive_summary.follow_up_priorities,
            "main_findings": [item.model_dump(mode="json") for item in report.executive_summary.main_findings],
        },
        metrics=metrics,
        capability_overview=report.product_overview.model_dump(mode="json"),
        evaluation_context=report.evaluation_context.model_dump(mode="json"),
        executive_summary=report.executive_summary.model_dump(mode="json"),
        dimensions=[item.model_dump(mode="json") for item in _ordered_dimensions(report)],
        experiments={
            "summary": report.experiment_overview.summary,
            "questions": [item.model_dump(mode="json") for item in report.experiment_overview.questions],
            "analysis": [item.model_dump(mode="json") for item in report.experiment_analysis],
        },
        scenario_stability=report.scenario_stability.model_dump(mode="json"),
        impact={
            **report.business_impact.model_dump(mode="json"),
            "findings": [item.model_dump(mode="json") for item in report.findings],
        },
        recommendations=[item.model_dump(mode="json") for item in report.recommendations],
        limitations=[item.model_dump(mode="json") for item in report.limitations],
        evidence_bundle={
            "status": raw_evidence.get("integrity", {}).get("status") or evidence_status,
            "summary": evidence_summary,
            "conditions": conditions,
        },
        technical_evidence={
            "product": [item.model_dump(mode="json") for item in report.evidence_explorer.product_evidence],
            "experiments": [item.model_dump(mode="json") for item in report.evidence_explorer.experiment_evidence],
            "records": raw_evidence.get("records") or [],
            "facts": raw_evidence.get("facts") or [],
            "supplementary": [item.model_dump(mode="json") for item in report.supplementary_evidence],
        },
        technical_metadata={
            "report_id": report.report_id,
            "schema_version": report.schema_version,
            "evaluation_id": report.evaluation.evaluation_id,
            "evaluation_type": report.evaluation_type,
            "report_hash": report.report_hash,
            "evidence_manifest_hash": report.provenance.evidence_manifest_hash,
            "evidence_schema_version": report.provenance.evidence_schema_version,
            "analyst_schema_version": report.provenance.analyst_schema_version,
            "analyst_provider": report.provenance.analyst_provider,
            "analyst_model": report.provenance.analyst_model,
            "analyst_request_id": report.provenance.analyst_request_id,
            "interpretation_evidence_level": report.provenance.interpretation_evidence_level,
            "raw_report_keys": sorted(report_payload),
        },
    )


def _project_context(report: ProductEvaluationReport, context: Mapping[str, object] | None) -> ReportProjectContext:
    scope = report.evaluation_plan.evaluation_scope if report.evaluation_plan else None
    values = {
        "project_id": report.subject.product_id,
        "project_name": report.subject.product_id,
        "purpose": report.product_overview.why_it_exists,
        "baseline": getattr(scope, "baseline_version", None) or "-",
        "candidate": getattr(scope, "candidate_version", None) or "-",
        "runtime": getattr(scope, "runtime_kind", None) or getattr(scope, "provider", None) or "-",
    }
    if context:
        values.update({key: str(value) for key, value in context.items() if value is not None})
    return ReportProjectContext(**values)


def _display_name(raw: str, project_id: str) -> str:
    match = re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*", raw.strip())
    return match.group(0) if match else (raw.strip() or project_id)


def _normalize_condition(condition: Mapping[str, Any], records: list[Mapping[str, Any]]) -> dict[str, Any]:
    observations = dict(condition.get("observations") or {})
    status = _condition_status(observations)
    references = list(condition.get("evidence_refs") or [])
    record = next(
        (
            item
            for item in records
            if set(references) & set(item.get("evidence_refs") or [])
        ),
        None,
    )
    return {
        "condition_id": condition.get("condition_id") or "condition",
        "experiment_id": condition.get("experiment_id") or "-",
        "scenario_id": condition.get("scenario_id") or "-",
        "label": condition.get("label") or condition.get("condition_id") or "Condition",
        "kind": _condition_kind(str(condition.get("label") or "")),
        "status": status,
        "observations": observations,
        "evidence_refs": references,
        "record": record,
    }


def _condition_status(observations: Mapping[str, Any]) -> str:
    failure_keys = ("oracle_verified", "target_completed", "runtime_completed", "deliverable_present", "structured_output", "constraint_adherence")
    if any(observations.get(key) is False for key in failure_keys):
        return "failed"
    if observations.get("oracle_verified") is True:
        return "passed"
    positive_keys = ("runtime_completed", "deliverable_present", "structured_output", "constraint_adherence")
    if any(key in observations for key in positive_keys) and all(observations.get(key) is True for key in positive_keys if key in observations):
        return "passed"
    return "review"


def _condition_kind(label: str) -> str:
    text = label.lower()
    if "启用" in label or "enabled" in text:
        return "enabled"
    if "移除" in label or "disabled" in text or "removal" in text:
        return "disabled"
    if "替换" in label or "equivalence" in text or "replacement" in text:
        return "replacement"
    return "condition"


def _ordered_dimensions(report: ProductEvaluationReport):
    order = {"trigger": 0, "execution": 1, "delivery": 2, "boundary": 3}
    return sorted(report.executive_summary.dimensions, key=lambda item: order.get(item.dimension, 99))


def _dump_item(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    return item


__all__ = [
    "NormalizedReport",
    "ReportProjectContext",
    "normalize_product_evaluation_report",
    "project_context_from_intelligence",
]
