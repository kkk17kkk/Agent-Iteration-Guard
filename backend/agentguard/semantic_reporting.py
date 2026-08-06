"""Product-semantic reports built from immutable evaluation evidence.

This module deliberately keeps three concerns separate:

* evidence adapters build a frozen, opaque evidence view;
* the Product Evaluation Analyst explains product meaning only;
* renderers project one validated report into different delivery formats.

The Analyst never receives a tool that can mutate evaluator records, and the
final report preserves the evaluator's evidence manifest hash.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .domain import ProviderBinding
from .evidence_bundle import EvidenceCondition, EvidenceFact, EvidenceIntegrity, EvidenceMetric, EvidenceRecord, ImmutableEvidenceBundle
from .evolution_types import ComponentType
from .product_reporting import SkillAblationArtifact, build_product_evaluation_evidence, load_skill_ablation_artifact
from .provider_runtime import ProviderRuntimeError, ProviderTurn


EvidenceLevel = Literal["verified", "derived", "inferred", "unresolved"]
ReportStatus = Literal["completed", "blocked"]
SemanticStatus = Literal["supported", "partially_supported", "mixed", "unresolved", "blocked"]
ImpactDirection = Literal["improved", "degraded", "unchanged", "mixed", "unknown"]
Severity = Literal["info", "low", "medium", "high", "critical"]


class ProductDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_type: ComponentType = "skill"
    component_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    product_responsibility: str = Field(min_length=1)
    user_job: str = Field(min_length=1)
    expected_behavior: list[str] = Field(default_factory=list)
    quality_dimensions: list[str] = Field(default_factory=list)
    boundary: list[str] = Field(default_factory=list)
    definition_status: Literal["declared", "candidate", "missing"] = "candidate"
    evidence_refs: list[str] = Field(default_factory=list)


class EvaluationScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_count: int = Field(ge=1)
    environment: str = Field(min_length=1)
    generalization: str = Field(min_length=1)


class AnalystInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aig.product-analyst-input.v1"] = "aig.product-analyst-input.v1"
    evaluation_type: Literal["skill_ablation"] = "skill_ablation"
    project_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    evaluation_name: str = Field(min_length=1)
    evaluation_question: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    scope: EvaluationScope
    product_definition: ProductDefinition
    evidence: ImmutableEvidenceBundle


class SkillOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    product_role: str = Field(min_length=1)
    user_value: list[str] = Field(min_length=1)
    expected_product_behavior: list[str] = Field(min_length=1)
    boundary_in_product_language: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class EvaluationGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    what_is_being_validated: list[str] = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    scope_statement: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class AnalystConditionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: str = Field(min_length=1)
    condition_label: str = Field(min_length=1)
    observed_behavior: str = Field(min_length=1)
    product_meaning: str = Field(min_length=1)
    interpretation_status: SemanticStatus
    evidence_refs: list[str] = Field(min_length=1)


class ExperimentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tests: list[AnalystConditionSummary] = Field(min_length=1)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1)
    finding_type: Literal["product_effect", "product_risk", "mechanism", "interaction", "limitation"]
    observation: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    product_meaning: str = Field(min_length=1)
    impact_dimension: str = Field(min_length=1)
    direction: ImpactDirection
    severity: Severity
    claim_status: SemanticStatus
    causal_scope: Literal["direct_observation", "controlled_comparison_supported", "associated_only", "unresolved"]
    scope_statement: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    uncertainties: list[str] = Field(default_factory=list)


class BusinessImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    affected_user_journey: str = Field(min_length=1)
    user_consequence: str = Field(min_length=1)
    product_value: list[str] = Field(default_factory=list)
    product_risks: list[str] = Field(default_factory=list)
    affected_capabilities: list[str] = Field(min_length=1)
    severity: Severity
    release_relevance: Literal["informational", "requires_review", "blocked_by_evidence"]
    evidence_refs: list[str] = Field(min_length=1)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str = Field(min_length=1)
    priority: Literal["low", "medium", "high", "critical"]
    target: str = Field(min_length=1)
    action: str = Field(min_length=1)
    reasoning: list[str] = Field(min_length=1)
    expected_product_effect: str = Field(min_length=1)
    validation_plan: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class Limitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    limitation_type: Literal["scope", "sample", "missing_evidence", "infrastructure", "causal"]
    evidence_refs: list[str] = Field(min_length=1)


class ProductEvaluationAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_overview: SkillOverview
    evaluation_goal: EvaluationGoal
    experiment_summary: ExperimentSummary
    findings: list[Finding] = Field(min_length=1)
    business_impact: BusinessImpact
    recommendation: list[Recommendation] = Field(min_length=1)
    limitations: list[Limitation] = Field(min_length=1)


class ReportTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1)
    observed_behavior: str = Field(min_length=1)
    product_meaning: str = Field(min_length=1)
    interpretation_status: SemanticStatus
    evidence_refs: list[str] = Field(min_length=1)


class ReportExperimentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tests: list[ReportTest] = Field(min_length=1)


class ReportProductEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_overview: SkillOverview
    evaluation_goal: EvaluationGoal
    experiment_summary: ReportExperimentSummary
    findings: list[Finding] = Field(min_length=1)
    business_impact: BusinessImpact
    recommendation: list[Recommendation] = Field(min_length=1)


class ProductEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["aig.product-evaluation-report.v1"] = "aig.product-evaluation-report.v1"
    report_id: str = Field(min_length=1)
    report_type: Literal["product_evaluation"] = "product_evaluation"
    evaluation_type: Literal["skill_ablation"] = "skill_ablation"
    status: ReportStatus
    subject: dict[str, str] = Field(min_length=1)
    product_context: ProductDefinition
    evaluation: dict[str, object] = Field(min_length=1)
    evidence: ImmutableEvidenceBundle
    product_evaluation: ReportProductEvaluation
    limitations: list[Limitation] = Field(min_length=1)
    provenance: dict[str, object] = Field(min_length=1)
    report_hash: str = Field(min_length=16)


class ProductReportProvider(Protocol):
    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ProviderTurn: ...


@dataclass(frozen=True)
class ProductAnalystRun:
    analysis: ProductEvaluationAnalysis
    provider: str
    model: str
    request_id: str


def _opaque_ref(raw_ref: str) -> str:
    return "evidence_" + hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()[:16]


def _opaque_id(prefix: str, raw_value: str) -> str:
    return prefix + "_" + hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:16]


def _status(artifact: SkillAblationArtifact, criterion_names: tuple[str, ...]) -> str:
    statuses = {item.name: item.status for item in artifact.verification.criteria}
    if any(statuses.get(name) == "failed" for name in criterion_names):
        return "failed"
    if artifact.verification.status == "infrastructure_error":
        return "infrastructure_error"
    return "passed"


def _condition_label(artifact: SkillAblationArtifact) -> str:
    return {
        "enabled": "启用 Skill 测试",
        "disabled": "移除 Skill 测试",
        "replacement": "Capability Equivalence Test",
    }[artifact.evidence.intervention]


def _product_definition_from_contract(artifact: SkillAblationArtifact) -> ProductDefinition:
    contract = artifact.contract
    return ProductDefinition(
        component_name=contract.skill_name,
        description=contract.deliverable,
        product_responsibility=f"在相关任务中可靠地产出{contract.deliverable}",
        user_job=f"获得可使用的{contract.deliverable}",
        expected_behavior=[contract.execution, contract.deliverable],
        quality_dimensions=["output_usability", "constraint_adherence"],
        boundary=[f"声明边界：{contract.boundary_expectation}"],
        definition_status="candidate",
    )


def product_definition_from_skill_artifact(artifact: SkillAblationArtifact) -> ProductDefinition:
    """Build product context for planning when no explicit definition is supplied."""

    return _product_definition_from_contract(artifact)


def build_skill_ablation_evidence_bundle(
    project_name: str,
    artifacts: list[SkillAblationArtifact],
    *,
    evaluation_name: str = "Skill Ablation",
    evaluation_request_id: str | None = None,
    baseline_version: str | None = None,
    candidate_version: str | None = None,
    evaluation_plan_id: str | None = None,
    experiment_ids_by_condition: dict[str, str] | None = None,
    scenario_ids_by_trial_ref: dict[str, str] | None = None,
) -> tuple[ImmutableEvidenceBundle, dict[str, object]]:
    if not artifacts:
        raise ValueError("A Product Semantic report requires at least one Skill-ablation artifact.")
    raw_evidence = build_product_evaluation_evidence(project_name, artifacts, evaluation_name=evaluation_name)
    conditions: list[EvidenceCondition] = []
    facts: list[EvidenceFact] = []
    order = {"enabled": 0, "disabled": 1, "replacement": 2}
    for artifact in sorted(artifacts, key=lambda item: (order[item.evidence.intervention], item.evidence.trial_ref)):
        evidence = artifact.evidence
        label = _condition_label(artifact)
        condition_id = _opaque_id("condition", evidence.skill_ablation_evidence_id)
        refs = {
            _opaque_ref(ref)
            for ref in _artifact_refs(artifact)
        }
        citation_refs = [sorted(refs)[0]]
        criteria = {item.name: item.status for item in artifact.verification.criteria}
        observations = {
            "structured_output": criteria.get("target_response_shape") == "passed",
            "constraint_adherence": criteria.get("generated_constraint_adherence") == "passed",
            "side_effect_boundary": "within_declared_boundary" if criteria.get("sqlite_write_boundary") == "passed" else "outside_or_unresolved",
            "fallback_used": evidence.fallback_used,
            "runtime_completed": evidence.runtime_error is None,
            "deliverable_present": criteria.get("deliverable") == "passed",
        }
        conditions.append(EvidenceCondition(
            condition_id=condition_id,
            scenario_id=(scenario_ids_by_trial_ref or {}).get(evidence.trial_ref),
            experiment_id=(experiment_ids_by_condition or {}).get(evidence.intervention),
            label=label,
            observations=observations,
            evidence_refs=citation_refs,
        ))
        fact_id = _opaque_id("fact", evidence.skill_ablation_evidence_id)
        facts.append(EvidenceFact(
            fact_id=fact_id,
            label=f"{label}的行为观察",
            fact_type="behavior_observation",
            value=observations,
            evidence_level="verified" if artifact.verification.status != "infrastructure_error" else "unresolved",
            evidence_refs=citation_refs,
        ))
    summary = raw_evidence.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("Skill-ablation evidence summary is invalid.")
    raw_artifacts = raw_evidence.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("Skill-ablation evidence artifacts are invalid.")
    records = [
        EvidenceRecord(
            record_id=_opaque_id("record", str(item.get("trial_ref", index))),
            record_type="artifact",
            source_ref=_opaque_ref(str(item.get("trial_ref", index))),
            payload={
                str(key): (
                    [_opaque_ref(str(ref)) for ref in value if ref]
                    if key == "evidence_refs" and isinstance(value, list)
                    else value
                )
                for key, value in item.items()
                if key not in {"trial_ref", "technical_evidence"}
            },
            evidence_refs=[_opaque_ref(str(ref)) for ref in item.get("evidence_refs", []) if ref],
        )
        for index, item in enumerate(raw_artifacts)
        if isinstance(item, dict)
    ]
    metrics = [
        EvidenceMetric(metric_id=_opaque_id("metric", key), name=key, value=value, unit="count")
        for key, value in summary.items()
        if isinstance(value, (int, float, str))
    ]
    integrity = EvidenceIntegrity(
        status="incomplete" if int(summary.get("infrastructure_error", 0)) else "complete",
        missing=["完整运行结论"] if int(summary.get("infrastructure_error", 0)) else [],
    )
    bundle = ImmutableEvidenceBundle(
        evaluation_id=_opaque_id("evaluation", str(raw_evidence["evidence_manifest_sha256"])),
        project_id=project_name,
        evaluation_name=evaluation_name,
        evaluation_type="skill_ablation",
        evaluation_request_id=evaluation_request_id,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        evaluation_plan_id=evaluation_plan_id,
        artifact_manifest_hash=str(raw_evidence["evidence_manifest_sha256"]),
        conditions=conditions,
        facts=facts,
        records=records,
        metrics=metrics,
        summary={key: value for key, value in summary.items() if isinstance(value, (int, float, str))},
        type_data={"interventions": [item.evidence.intervention for item in artifacts]},
        integrity=integrity,
    )
    return bundle, raw_evidence


def build_skill_ablation_analyst_input(
    project_name: str,
    artifacts: list[SkillAblationArtifact],
    *,
    product_definition: ProductDefinition | None = None,
    evaluation_name: str = "Skill Ablation",
    evidence_bundle: ImmutableEvidenceBundle | None = None,
) -> tuple[AnalystInput, dict[str, object]]:
    bundle = evidence_bundle
    if bundle is None:
        bundle, raw_evidence = build_skill_ablation_evidence_bundle(project_name, artifacts, evaluation_name=evaluation_name)
    else:
        raw_evidence = build_product_evaluation_evidence(project_name, artifacts, evaluation_name=evaluation_name)
    definition = product_definition or _product_definition_from_contract(artifacts[0])
    input_data = AnalystInput(
        project_id=project_name,
        product_name=project_name,
        evaluation_name=evaluation_name,
        evaluation_question=f"{definition.component_name} 是否改善用户得到的产品结果？",
        hypothesis=f"移除或替换 {definition.component_name} 后，产品结果可能无法稳定满足声明的约束。",
        scope=EvaluationScope(task_count=len(artifacts), environment="approved_evaluation_environment", generalization="仅限当前受控任务和实验样本"),
        product_definition=definition,
        evidence=bundle,
    )
    return input_data, raw_evidence


def _artifact_refs(artifact: SkillAblationArtifact) -> set[str]:
    refs = set(artifact.evidence.boundary_evidence_refs)
    refs.update(item.evidence_ref for item in artifact.evidence.trace_events)
    for criterion in artifact.verification.criteria:
        refs.update(criterion.evidence_refs)
    for criterion in artifact.evidence.target_criteria:
        refs.update(criterion.evidence_refs)
    if artifact.evidence.deliverable_evidence_ref:
        refs.add(artifact.evidence.deliverable_evidence_ref)
    refs.update(artifact.evidence.sut_provider_request_ids)
    return {ref for group in refs for ref in (group if isinstance(group, list) else [group]) if ref}


def _analyst_tool_spec() -> dict[str, object]:
    schema = ProductEvaluationAnalysis.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": "submit_product_evaluation_analysis",
            "description": "Submit an evidence-linked product-language interpretation without changing evaluator facts.",
            "parameters": schema,
        },
    }


def generate_product_evaluation_analysis_with_provider(
    analyst_input: AnalystInput,
    *,
    provider: ProductReportProvider,
    binding: ProviderBinding,
    forbidden_tokens: set[str] | None = None,
) -> ProductAnalystRun:
    if binding.role != "control_plane":
        raise ValueError("Product Evaluation Analyst requires a control_plane ProviderBinding.")
    system = (
        "你是 AgentGuard Product Evaluation Analyst。你不是 Verifier，也不能重新判断实验结果。"
        "输入中的 evidence 是不可变事实，不能修改、重算、补写或升级其证据等级。"
        "你的职责是根据 product_definition，把机器事实翻译为产品语言，解释用户影响并提出可复验建议。"
        "不要输出 Trial ID、Arm、verifier 状态、post-trigger、provider request ID 或内部 evaluator 字段。"
        "条件必须使用输入中的产品化 condition label。每个观察、解释、影响和建议都必须引用已有 evidence_refs。"
        "产品职责只能来自 product_definition；如果定义状态为 candidate 或 missing，必须明确说明不确定性。"
        "不得做发布批准，不得把单个受控任务外推为普遍能力，不确定时使用 unresolved。"
    )
    turn = provider.complete(
        [{"role": "system", "content": system}, {"role": "user", "content": analyst_input.model_dump_json(ensure_ascii=False)}],
        [_analyst_tool_spec()],
    )
    if len(turn.tool_calls) != 1 or turn.tool_calls[0].name != "submit_product_evaluation_analysis":
        raise ProviderRuntimeError("Product Evaluation Analyst did not submit its required structured analysis.")
    raw = turn.tool_calls[0].arguments
    try:
        analysis = ProductEvaluationAnalysis.model_validate(raw)
    except ValueError as error:
        raise ProviderRuntimeError("Product Evaluation Analyst returned an invalid semantic report structure.") from error
    _validate_analysis(analysis, analyst_input, forbidden_tokens or set())
    return ProductAnalystRun(analysis, binding.provider, binding.model, turn.request_id)


def _all_evidence_refs(analyst_input: AnalystInput) -> set[str]:
    return {ref for fact in analyst_input.evidence.facts for ref in fact.evidence_refs} | {
        ref for condition in analyst_input.evidence.conditions for ref in condition.evidence_refs
    }


def _validate_analysis(analysis: ProductEvaluationAnalysis, analyst_input: AnalystInput, forbidden_tokens: set[str]) -> None:
    allowed_refs = _all_evidence_refs(analyst_input) | set(analyst_input.product_definition.evidence_refs)
    condition_ids = {item.condition_id for item in analyst_input.evidence.conditions}
    if {item.condition_id for item in analysis.experiment_summary.tests} != condition_ids:
        raise ProviderRuntimeError("Product analysis must cover every persisted product-language condition exactly once.")
    for item in analysis.experiment_summary.tests:
        if item.condition_label not in {condition.label for condition in analyst_input.evidence.conditions}:
            raise ProviderRuntimeError("Product analysis used an unknown condition label.")
        _validate_refs(item.evidence_refs, allowed_refs)
    for item in analysis.findings:
        _validate_refs(item.evidence_refs, allowed_refs)
    _validate_refs(analysis.skill_overview.evidence_refs, allowed_refs)
    _validate_refs(analysis.evaluation_goal.evidence_refs, allowed_refs)
    _validate_refs(analysis.business_impact.evidence_refs, allowed_refs | {item.finding_id for item in analysis.findings})
    finding_ids = {item.finding_id for item in analysis.findings}
    for item in analysis.recommendation:
        if not set(item.evidence_refs) <= (allowed_refs | finding_ids):
            raise ProviderRuntimeError("Product recommendation cited an unknown finding or evidence reference.")
    for limitation in analysis.limitations:
        _validate_refs(limitation.evidence_refs, allowed_refs)
    strings = _strings(analysis.model_dump(mode="json"))
    leaked = {token for token in forbidden_tokens if token and any(token in value for value in strings)}
    leaked.update(token for token in ("post-trigger", "verifier passed", "verifier failed") if any(token in value.lower() for value in strings))
    if leaked:
        raise ProviderRuntimeError(f"Product analysis exposed internal evaluation fields: {sorted(leaked)}")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    return []


def _validate_refs(refs: list[str], allowed: set[str]) -> None:
    if not refs or not set(refs) <= allowed:
        raise ProviderRuntimeError("Product semantic text contains an invalid or missing evidence reference.")


def assemble_product_evaluation_report(
    analyst_input: AnalystInput,
    run: ProductAnalystRun,
) -> ProductEvaluationReport:
    _validate_analysis(run.analysis, analyst_input, set())
    analysis = run.analysis
    report_product = ReportProductEvaluation(
        skill_overview=analysis.skill_overview,
        evaluation_goal=analysis.evaluation_goal,
        experiment_summary=ReportExperimentSummary(tests=[
            ReportTest(
                condition=item.condition_label,
                observed_behavior=item.observed_behavior,
                product_meaning=item.product_meaning,
                interpretation_status=item.interpretation_status,
                evidence_refs=item.evidence_refs,
            )
            for item in analysis.experiment_summary.tests
        ]),
        findings=analysis.findings,
        business_impact=analysis.business_impact,
        recommendation=analysis.recommendation,
    )
    report_without_hash = {
        "schema_version": "aig.product-evaluation-report.v1",
        "report_id": _opaque_id("report", analyst_input.evidence.artifact_manifest_hash),
        "report_type": "product_evaluation",
        "evaluation_type": "skill_ablation",
        "status": "completed",
        "subject": {
            "product_id": analyst_input.project_id,
            "product_name": analyst_input.product_name,
            "component_type": analyst_input.product_definition.component_type,
            "component_name": analyst_input.product_definition.component_name,
        },
        "product_context": analyst_input.product_definition.model_dump(mode="json"),
        "evaluation": {
            "evaluation_name": analyst_input.evaluation_name,
            "question": analyst_input.evaluation_question,
            "hypothesis": analyst_input.hypothesis,
            "scope": analyst_input.scope.model_dump(mode="json"),
            "evaluation_status": "evidence_complete" if analyst_input.evidence.integrity.status == "complete" else "evidence_limited",
            "release_status": "not_evaluated",
        },
        "evidence": analyst_input.evidence.model_dump(mode="json"),
        "product_evaluation": report_product.model_dump(mode="json"),
        "limitations": [item.model_dump(mode="json") for item in analysis.limitations],
        "provenance": {
            "evidence_manifest_hash": analyst_input.evidence.artifact_manifest_hash,
            "analyst_provider": run.provider,
            "analyst_model": run.model,
            "analyst_evidence_level": "inferred",
        },
    }
    report_hash = hashlib.sha256(json.dumps(report_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ProductEvaluationReport(**report_without_hash, report_hash=report_hash)


def render_product_evaluation_markdown(report: ProductEvaluationReport) -> str:
    evaluation = report.evaluation
    product = report.product_evaluation
    lines = [
        f"# {report.subject['component_name']} 产品评估",
        "",
        "## Skill 在产品中的职责",
        "",
        product.skill_overview.product_role,
        "",
        "## 实验验证目标",
        "",
        product.evaluation_goal.question,
        "",
        product.evaluation_goal.why_it_matters,
        "",
        "## 实验观察",
        "",
    ]
    for item in product.experiment_summary.tests:
        lines.extend([f"### {item.condition}", "", item.observed_behavior, "", item.product_meaning, ""])
    lines.extend(["## 产品影响", "", product.business_impact.user_consequence, ""])
    for finding in product.findings:
        lines.extend([f"- {finding.product_meaning}（{finding.impact_dimension}，{finding.severity}）"])
    lines.extend(["", "## 推荐行动", ""])
    for item in product.recommendation:
        lines.extend([f"- **{item.priority}**：{item.action}", f"  - 验证：{'；'.join(item.validation_plan)}"])
    lines.extend(["", "## 限制", ""])
    lines.extend(f"- {item.statement}" for item in report.limitations)
    lines.extend(["", f"报告状态：{report.status}", f"评估状态：{evaluation['evaluation_status']}"])
    return "\n".join(lines) + "\n"


def render_product_evaluation_html(report: ProductEvaluationReport) -> str:
    product = report.product_evaluation
    tests = "".join(
        f"<article class='test'><h3>{_escape(item.condition)}</h3><p>{_escape(item.observed_behavior)}</p><p class='meaning'>{_escape(item.product_meaning)}</p></article>"
        for item in product.experiment_summary.tests
    )
    findings = "".join(
        f"<li><strong>{_escape(item.impact_dimension)}</strong>：{_escape(item.product_meaning)}<span>{_escape(item.severity)}</span></li>"
        for item in product.findings
    )
    recommendations = "".join(
        f"<li><strong>{_escape(item.priority)}</strong>：{_escape(item.action)}<small>验证：{_escape('；'.join(item.validation_plan))}</small></li>"
        for item in product.recommendation
    )
    limitations = "".join(f"<li>{_escape(item.statement)}</li>" for item in report.limitations)
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_escape(report.subject['component_name'])} 产品评估</title><style>
body{{margin:0;background:#f5f7fb;color:#1a2433;font:16px/1.7 'Microsoft YaHei',system-ui,sans-serif}}main{{max-width:980px;margin:auto;padding:42px 22px 64px}}section{{background:#fff;border:1px solid #dfe5ef;border-radius:16px;padding:24px;margin:16px 0}}h1{{margin:0 0 8px}}h2{{font-size:20px}}h3{{font-size:17px;margin-bottom:8px}}.eyebrow{{color:#2458d3;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}}.lede{{color:#637085;font-size:18px}}.test{{border-top:1px solid #e5e9f1;padding:14px 0}}.test:first-child{{border-top:0}}.meaning{{color:#2458d3;font-weight:600}}li{{margin:10px 0}}li span{{float:right;color:#9a5b00;font-size:13px}}small{{display:block;color:#637085;margin-top:4px}}footer{{color:#637085;font-size:13px;margin-top:22px}}</style></head><body><main><p class='eyebrow'>Agent Iteration Guard · Product Evaluation</p><h1>{_escape(report.subject['component_name'])} 产品评估</h1><p class='lede'>{_escape(product.skill_overview.product_role)}</p><section><p class='eyebrow'>Evaluation Goal</p><h2>{_escape(product.evaluation_goal.question)}</h2><p>{_escape(product.evaluation_goal.why_it_matters)}</p></section><section><p class='eyebrow'>Experiment Summary</p>{tests}</section><section><p class='eyebrow'>Business Impact</p><p>{_escape(product.business_impact.user_consequence)}</p><ul>{findings}</ul></section><section><p class='eyebrow'>Recommendation</p><ul>{recommendations}</ul></section><section><p class='eyebrow'>Limitations</p><ul>{limitations}</ul></section><footer>报告状态：{_escape(report.status)} · 评估状态：{_escape(str(report.evaluation['evaluation_status']))} · 证据摘要哈希：{_escape(report.report_hash)}</footer></main></body></html>"""


def _escape(value: object) -> str:
    return html.escape(str(value))


def write_product_evaluation_outputs(
    output_dir: Path,
    report: ProductEvaluationReport,
    raw_evidence: dict[str, object],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "evidence": output_dir / "product-evaluation-evidence.json",
        "report": output_dir / "product-evaluation-report.json",
        "html": output_dir / "product-evaluation-report.html",
        "markdown": output_dir / "product-evaluation-report.md",
    }
    paths["evidence"].write_text(json.dumps(raw_evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["report"].write_text(report.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    paths["html"].write_text(render_product_evaluation_html(report), encoding="utf-8")
    paths["markdown"].write_text(render_product_evaluation_markdown(report), encoding="utf-8")
    return paths


def product_evaluation_api_payload(report: ProductEvaluationReport) -> dict[str, object]:
    """Return the exact portable report object used by API consumers."""
    return report.model_dump(mode="json")


__all__ = [
    "AnalystInput",
    "ImmutableEvidenceBundle",
    "ProductDefinition",
    "ProductEvaluationAnalysis",
    "ProductEvaluationReport",
    "ProductReportProvider",
    "assemble_product_evaluation_report",
    "build_skill_ablation_analyst_input",
    "generate_product_evaluation_analysis_with_provider",
    "load_skill_ablation_artifact",
    "product_evaluation_api_payload",
    "product_definition_from_skill_artifact",
    "render_product_evaluation_html",
    "render_product_evaluation_markdown",
    "write_product_evaluation_outputs",
]
