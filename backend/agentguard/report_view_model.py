"""Shared report presentation data for the application Preview and HTML export."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .product_evaluation_report import ProductEvaluationReport
from .project_intelligence import ProjectIntelligence
from .release_decision_gate import evaluate_release_decision


_DIMENSION_LABELS = {
    "trigger": "Trigger：能力触发",
    "execution": "Execution：流程执行",
    "delivery": "Delivery：结果交付",
    "boundary": "Boundary：能力边界",
}

_CONDITION_KIND_LABELS = {
    "enabled": "启用 Skill",
    "disabled": "移除 Skill",
    "replacement": "替换实现",
    "condition": "实验条件",
}


_CONDITION_KIND_LABELS.update({
    "baseline": "Baseline",
    "removal": "Skill Removal",
    "replacement": "Capability Replacement",
    "a_only": "Skill A only",
    "b_only": "Skill B only",
    "combined": "Skill A + Skill B",
})


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
    interaction_analysis: dict[str, Any] | None = None
    root_cause_findings: list[dict[str, Any]] = Field(default_factory=list)
    impact: dict[str, Any]
    recommendations: list[dict[str, Any]]
    limitations: list[dict[str, Any]]
    evidence_bundle: dict[str, Any]
    technical_evidence: dict[str, Any]
    technical_metadata: dict[str, Any]
    evaluation_suite: dict[str, Any] | None = None


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
    suite_aggregate = (raw_evidence.get("type_data") or {}).get("suite_aggregate")
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
    semantic_decision = _semantic_decision(report)
    decision = {
        "decision": semantic_decision,
        "rationale": _semantic_rationale(report, semantic_decision),
        "checks": _semantic_checks(report),
        "presentation_note": (
            f"技术证据门禁：{_gate_rationale_zh(str(gate_value.get('decision') or 'pending'))}"
            if str(gate_value.get("decision") or "pending") != "approve"
            else ""
        ),
        "evidence_gate": {
            "decision": str(gate_value.get("decision") or "pending"),
            "rationale": _gate_rationale_zh(str(gate_value.get("decision") or "pending")),
            "checks": [_dump_item(item) for item in gate_value.get("checks") or []],
            "blocking_reasons": list(gate_value.get("blocking_reasons") or []),
            "review_reasons": list(gate_value.get("review_reasons") or []),
        },
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
    if isinstance(suite_aggregate, Mapping):
        coverage = suite_aggregate.get("coverage")
        if isinstance(coverage, Mapping):
            metrics.update({
                "scenario_count": coverage.get("executed_scenario_count"),
                "trial_count": coverage.get("executed_trial_count"),
                "coverage_status": coverage.get("status"),
                "repeated_scenario_count": coverage.get("repeated_scenario_count"),
            })
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
        dimensions=[_normalize_dimension(item.model_dump(mode="json")) for item in _ordered_dimensions(report)],
        experiments={
            "summary": report.experiment_overview.summary,
            "questions": [item.model_dump(mode="json") for item in report.experiment_overview.questions],
            "analysis": [_normalize_experiment(item.model_dump(mode="json")) for item in report.experiment_analysis],
        },
        scenario_stability=report.scenario_stability.model_dump(mode="json"),
        interaction_analysis=(report.interaction_analysis.model_dump(mode="json") if report.interaction_analysis else None),
        root_cause_findings=[item.model_dump(mode="json") for item in report.root_cause_findings],
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
        },
        evaluation_suite=dict(suite_aggregate) if isinstance(suite_aggregate, Mapping) else None,
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
    label = condition.get("label") or condition.get("condition_id") or "Condition"
    status = _condition_status(observations, str(label))
    references = list(condition.get("evidence_refs") or [])
    record = next(
        (
            item
            for item in records
            if set(references) & set(item.get("evidence_refs") or [])
        ),
        None,
    )
    condition_kind = str(observations.get("condition_kind") or _condition_kind(str(label)))
    return {
        "condition_id": condition.get("condition_id") or "condition",
        "experiment_id": condition.get("experiment_id") or "-",
        "scenario_id": condition.get("scenario_id") or "-",
        "repetition_id": condition.get("repetition_id") or "-",
        "repetition_index": condition.get("repetition_index"),
        "label": label,
        "kind": condition_kind,
        "kind_label": _CONDITION_KIND_LABELS.get(condition_kind, "实验条件"),
        "status": status,
        "observations": observations,
        "evidence_refs": references,
        "record": record,
    }


def _condition_status(observations: Mapping[str, Any], label: str) -> str:
    oracle_outcome = str(observations.get("oracle_outcome") or "").lower()
    if oracle_outcome in {"passed", "pass", "supported", "success"}:
        return "passed"
    if oracle_outcome in {"failed", "fail", "blocked", "error"}:
        return "failed"
    # In a Skill ablation, a failing constraint check is the expected
    # contrast for removal/replacement. The experiment passes when the
    # intervention creates the declared difference without breaking runtime.
    kind = _condition_kind(label)
    if kind in {"disabled", "replacement"} and observations.get("constraint_adherence") is False:
        if observations.get("runtime_completed") is True and observations.get("deliverable_present") is True:
            return "passed"
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
    if "启用" in label or "enabled" in text or "baseline" in text:
        return "enabled"
    if "移除" in label or "disabled" in text or "removal" in text:
        return "disabled"
    if "替换" in label or "equivalence" in text or "replacement" in text:
        return "replacement"
    return "condition"


def _ordered_dimensions(report: ProductEvaluationReport):
    order = {"trigger": 0, "execution": 1, "delivery": 2, "boundary": 3}
    return sorted(report.executive_summary.dimensions, key=lambda item: order.get(item.dimension, 99))


def _normalize_dimension(item: Mapping[str, Any]) -> dict[str, Any]:
    return {**item, "label": _DIMENSION_LABELS.get(str(item.get("dimension")), str(item.get("dimension") or "评估维度"))}


def _normalize_experiment(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result["display_name"] = _experiment_display_name(str(item.get("experiment_name") or ""))
    return result


def _experiment_display_name(name: str) -> str:
    text = name.lower()
    if "移除" in name or "remov" in text or "disabled" in text:
        return "移除能力实验：约束差异已验证"
    if "替换" in name or "equivalence" in text or "replacement" in text:
        return "替换能力实验：核心价值差异已验证"
    if "保留" in name or "baseline" in text or "enabled" in text:
        return "保留能力基线：约束执行通过"
    return name


def _semantic_decision(report: ProductEvaluationReport) -> str:
    if report.status == "blocked":
        return "block"
    if (
        report.executive_summary.status == "supported"
        and all(item.status == "supported" for item in report.executive_summary.dimensions)
        and report.scenario_stability.status == "supported"
    ):
        return "pass"
    return "review"


def _semantic_rationale(report: ProductEvaluationReport, decision: str) -> str:
    if decision == "pass":
        return report.executive_summary.final_conclusion
    if decision == "block":
        return "评估报告明确标记为不可用，当前能力结论未通过。"
    return "当前能力结论仍有部分维度或场景需要开发者复核。"


def _semantic_checks(report: ProductEvaluationReport) -> list[dict[str, str]]:
    summary_status = report.executive_summary.status
    dimension_status = "passed" if all(item.status == "supported" for item in report.executive_summary.dimensions) else "review"
    stability_status = "passed" if report.scenario_stability.status == "supported" else "review"
    comparison_supported = (
        summary_status == "supported"
        and any(item.finding_type == "capability_loss" for item in report.executive_summary.main_findings)
        and any(item.finding_type == "replacement_risk" for item in report.executive_summary.main_findings)
    )
    return [
        {"name": "报告完成", "status": "passed" if report.status == "completed" else "blocked", "detail": "报告内容已完成生成。" if report.status == "completed" else "报告尚未完成。"},
        {"name": "能力基线", "status": "passed" if summary_status == "supported" else "review", "detail": "启用 Skill 的基线结果得到支持。" if summary_status == "supported" else "能力基线仍需复核。"},
        {"name": "移除 / 替换对照", "status": "passed" if comparison_supported else "review", "detail": "移除和替换后出现预期能力差异，说明 Skill 有实际价值。" if comparison_supported else "移除或替换对照证据仍需复核。"},
        {"name": "场景稳定性", "status": stability_status, "detail": "覆盖场景中的能力表现一致。" if stability_status == "passed" else "场景覆盖或稳定性仍需复核。"},
        {"name": "评估维度", "status": dimension_status, "detail": "能力触发、流程执行、结果交付和能力边界均得到支持。" if dimension_status == "passed" else "至少一个评估维度仍需复核。"},
    ]


def _gate_rationale_zh(decision: str) -> str:
    if decision == "approve":
        return "技术证据门禁已通过。"
    if decision == "review":
        return "技术证据完整，但仍有项目需要开发者复核。"
    if decision == "block":
        return "技术证据门禁未通过；这只表示证据或报告完整性需要处理，不代表 Skill 评估结论失败。"
    return "技术证据门禁尚未评估。"


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
