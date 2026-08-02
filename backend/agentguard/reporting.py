from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

from .domain import (
    AgentEvolutionCase,
    EvaluationHypothesis,
    EvolutionAgentRun,
    EvolutionComparison,
    EvolutionProviderUsage,
    EvolutionTrial,
    EvolutionVerification,
    MemoryEntry,
    ProviderBinding,
    ReportFact,
    ReportManifest,
    ReportNarrative,
    ReportNarrativeSection,
    SkillAblationAnalysis,
    SkillAblationEvidence,
    SkillAblationVerification,
    SkillDimensionNarrative,
    StalePropagation,
)
from .store import Store
from .targets import TargetObservation, TerminalArtifact


class EvolutionReportingError(RuntimeError):
    pass


REPORT_SYSTEM_PROMPT = (
    "你是 Agent Iteration Guard 的报告 Agent。写作前必须读取不可变 ReportManifest。"
    "每轮只能调用一个工具并等待 observation，只能引用 Manifest 提供的 fact ID。"
    "标题、摘要、章节解释和限制必须使用简体中文。你的解释属于 inferred 阅读层，"
    "不得修改事实、指标、evaluation Gate 或 release status。"
)


_CATEGORY_LABELS = {
    "baseline_verifier": "基线验证",
    "candidate_verifier": "候选验证",
    "pair_equivalence": "配对等价性",
    "write_boundary": "写入边界",
    "control_plane_agent": "控制平面 Agent",
    "version_memory": "版本记忆",
    "skill_trigger": "Skill 触发",
    "skill_trace": "触发后的执行过程",
    "skill_deliverable": "交付结果",
    "skill_boundary": "边界行为",
    "skill_trigger_analysis": "触发解读",
    "skill_trace_analysis": "过程解读",
    "skill_deliverable_analysis": "交付解读",
    "skill_boundary_analysis": "边界解读",
}

_METRIC_LABELS = {
    "paired_trials": "配对 Trial 数",
    "baseline_verification_passes": "基线通过数",
    "candidate_verification_passes": "候选通过数",
    "control_plane_model_calls": "控制平面模型调用",
    "control_plane_tool_calls": "控制平面工具调用",
    "control_plane_input_tokens": "输入 Token",
    "control_plane_output_tokens": "输出 Token",
    "successful_run_cost_usd": "成功运行成本 USD",
    "case_binding_cost_before_report_usd": "报告前累计成本 USD",
    "approved_control_plane_and_report_budget_usd": "批准预算 USD",
    "budget_remaining_before_report_usd": "报告前剩余预算 USD",
    "skill_ablation_runs": "Skill 消融运行数",
    "skill_ablation_passes": "完整证据通过数",
}

_SKILL_DIMENSIONS = {
    "trigger": "skill_trigger",
    "trace": "skill_trace",
    "deliverable": "skill_deliverable",
    "boundary": "skill_boundary",
}

_SKILL_DIMENSION_LABELS = {
    "trigger": "触发时刻",
    "trace": "触发后的过程",
    "deliverable": "任务交付物",
    "boundary": "边界与异常",
}

_GATE_LABELS = {
    "candidate_behavior_supported": "候选行为得到支持",
    "not_supported": "候选行为未得到支持",
    "blocked": "评测受阻",
}


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _manifest_fingerprint(manifest: ReportManifest) -> str:
    payload = manifest.model_dump(exclude={"report_manifest_id", "manifest_fingerprint", "created_at"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _criterion_summary(verification: EvolutionVerification) -> str:
    return "；".join(
        f"{criterion.name}={criterion.status}（{criterion.detail}）"
        for criterion in verification.criteria
    ) or "未记录判据"


def build_report_manifest(
    store: Store,
    project_id: str,
    evolution_case_id: str,
    control_plane_run_id: str,
) -> ReportManifest:
    case = store.get("agent_evolution_case", evolution_case_id, AgentEvolutionCase)
    run = store.get("evolution_agent_run", control_plane_run_id, EvolutionAgentRun)
    if not case or case.project_id != project_id or not run or run.project_id != project_id:
        raise EvolutionReportingError("Case and control-plane run must exist in the same project")
    comparisons = [
        item for item in store.list("evolution_comparison", EvolutionComparison, project_id)
        if item.evolution_case_id == evolution_case_id and item.status == "compared"
    ]
    if not comparisons:
        raise EvolutionReportingError("A deterministically recomputed comparison is required")
    comparison = comparisons[-1]
    trials = [
        item for item in store.list("evolution_trial", EvolutionTrial, project_id)
        if item.evolution_case_id == evolution_case_id
    ]
    verification_by_trial = {
        item.evolution_trial_id: item
        for item in store.list("evolution_verification", EvolutionVerification, project_id)
        if item.evolution_case_id == evolution_case_id
    }
    selected_verifications = set(comparison.evidence_ids)
    paired: list[
        tuple[EvolutionTrial, EvolutionVerification, EvolutionTrial, EvolutionVerification]
    ] = []
    for trial_index in sorted({item.trial_index for item in trials}):
        by_role: dict[str, list[tuple[EvolutionTrial, EvolutionVerification]]] = {
            "baseline": [], "candidate": []
        }
        for trial in trials:
            verification = verification_by_trial.get(trial.evolution_trial_id)
            if (
                trial.trial_index == trial_index
                and verification
                and verification.evolution_verification_id in selected_verifications
            ):
                by_role[trial.revision_role].append((trial, verification))
        if not by_role["baseline"] and not by_role["candidate"]:
            continue
        if len(by_role["baseline"]) != 1 or len(by_role["candidate"]) != 1:
            raise EvolutionReportingError(
                f"Comparison evidence for pair {trial_index} is incomplete or ambiguous"
            )
        baseline, baseline_verification = by_role["baseline"][0]
        candidate, candidate_verification = by_role["candidate"][0]
        if (
            baseline.environment_fingerprint != candidate.environment_fingerprint
            or baseline.request_fingerprint != candidate.request_fingerprint
            or baseline.initial_state_ref != candidate.initial_state_ref
        ):
            raise EvolutionReportingError(f"Pair {trial_index} inputs are not equivalent")
        paired.append((baseline, baseline_verification, candidate, candidate_verification))
    if not paired:
        raise EvolutionReportingError("Comparison does not reference a complete paired trial")
    environment_fingerprints = {pair[0].environment_fingerprint for pair in paired}
    if len(environment_fingerprints) != 1:
        raise EvolutionReportingError("Compared pairs do not share one environment fingerprint")
    baseline_verifications = [pair[1] for pair in paired]
    candidate_verifications = [pair[3] for pair in paired]
    baseline_evidence_refs = [
        ref for verification in baseline_verifications
        for ref in (verification.evolution_verification_id, *verification.evidence_refs)
    ]
    candidate_evidence_refs = [
        ref for verification in candidate_verifications
        for ref in (verification.evolution_verification_id, *verification.evidence_refs)
    ]
    facts = [
        ReportFact(
            category="baseline_verifier",
            statement="；".join(
                f"Trial {pair[0].trial_index} 基线={pair[1].status}：{_criterion_summary(pair[1])}"
                for pair in paired
            ),
            evidence_level="verified",
            evidence_refs=baseline_evidence_refs,
        ),
        ReportFact(
            category="candidate_verifier",
            statement="；".join(
                f"Trial {pair[2].trial_index} 候选={pair[3].status}：{_criterion_summary(pair[3])}"
                for pair in paired
            ),
            evidence_level="verified",
            evidence_refs=candidate_evidence_refs,
        ),
        ReportFact(
            category="pair_equivalence",
            statement=f"{len(paired)} 对基线/候选 Trial 均使用相同的环境、请求和初始状态指纹。",
            evidence_level="verified",
            evidence_refs=[trial.evolution_trial_id for pair in paired for trial in (pair[0], pair[2])],
        ),
        ReportFact(
            category="control_plane_agent",
            statement=(
                f"真实控制平面运行完成 {len(run.model_call_ids)} 次模型调用和 "
                f"{len(run.tool_call_ids)} 次工具调用；terminal_reason={run.terminal_reason}。"
            ),
            evidence_level="verified",
            evidence_refs=[run.evolution_agent_run_id, *run.model_call_ids],
        ),
    ]
    write_boundaries = [
        next((item for item in verification.criteria if item.name == "write_boundary"), None)
        for verification in (*baseline_verifications, *candidate_verifications)
    ]
    if all(write_boundaries):
        facts.append(ReportFact(
            category="write_boundary",
            statement=(
                "写入边界：基线="
                f"{','.join(item.status for item in write_boundaries[:len(paired)] if item)}，"
                "候选="
                f"{','.join(item.status for item in write_boundaries[len(paired):] if item)}。"
            ),
            evidence_level="verified",
            evidence_refs=[
                verification.evolution_verification_id
                for verification in (*baseline_verifications, *candidate_verifications)
            ],
        ))
    skill_verifications = [
        item for item in store.list("skill_ablation_verification", SkillAblationVerification, project_id)
        if item.evolution_case_id == evolution_case_id
    ]
    skill_evidence = {
        item.skill_ablation_evidence_id: item
        for item in store.list("skill_ablation_evidence", SkillAblationEvidence, project_id)
        if item.evolution_case_id == evolution_case_id
    }
    skill_analyses = {
        item.skill_ablation_evidence_id: item
        for item in store.list("skill_ablation_analysis", SkillAblationAnalysis, project_id)
        if item.evolution_case_id == evolution_case_id
    }
    for dimension, criterion_name in (
        ("trigger", "skill_trigger"),
        ("trace", "post_trigger_trace"),
        ("deliverable", "deliverable"),
        ("boundary", "boundary_behavior"),
    ):
        observed = [
            (verification, next((criterion for criterion in verification.criteria if criterion.name == criterion_name), None))
            for verification in skill_verifications
        ]
        observed = [(verification, criterion) for verification, criterion in observed if criterion]
        if not observed:
            continue
        statuses = [criterion.status for _, criterion in observed]
        refs = [
            ref for verification, criterion in observed
            for ref in (verification.skill_ablation_verification_id, *criterion.evidence_refs)
        ]
        facts.append(ReportFact(
            category=_SKILL_DIMENSIONS[dimension],
            statement=(
                f"{_SKILL_DIMENSION_LABELS[dimension]}：在 {len(observed)} 次 Skill 消融运行中，"
                f"{sum(status == 'passed' for status in statuses)} 次证据完整，"
                f"{sum(status == 'failed' for status in statuses)} 次未达到要求。"
            ),
            evidence_level="verified",
            evidence_refs=list(dict.fromkeys(refs)),
        ))
        analysis_values = [
            getattr(analysis, f"{dimension}_analysis")
            for evidence_id, analysis in skill_analyses.items()
            if evidence_id in {verification.skill_ablation_evidence_id for verification, _ in observed}
        ]
        if analysis_values:
            facts.append(ReportFact(
                category=f"{_SKILL_DIMENSIONS[dimension]}_analysis",
                statement="；".join(analysis_values),
                evidence_level="inferred",
                evidence_refs=[
                    analysis.skill_ablation_analysis_id
                    for evidence_id, analysis in skill_analyses.items()
                    if evidence_id in {verification.skill_ablation_evidence_id for verification, _ in observed}
                ],
            ))
    all_binding_runs = [
        item for item in store.list("evolution_agent_run", EvolutionAgentRun, project_id)
        if item.provider_binding_id == run.provider_binding_id
    ]
    binding = store.get("provider_binding", run.provider_binding_id, ProviderBinding)
    if not binding:
        raise EvolutionReportingError("Control-plane ProviderBinding is unavailable")
    binding_spend = sum(item.spent_cost_usd for item in all_binding_runs)
    usages = [
        item for item in store.list("evolution_provider_usage", EvolutionProviderUsage, project_id)
        if item.evolution_agent_run_id == run.evolution_agent_run_id
    ]
    stale = [
        item for item in store.list("stale_propagation", StalePropagation, project_id)
        if item.evolution_changeset_id == case.evolution_changeset_id
    ]
    memories = store.list("memory_entry", MemoryEntry, project_id)
    if stale:
        facts.append(ReportFact(
            category="version_memory",
            statement=(
                f"stale propagation 将 {len(stale[-1].stale_memory_ids)} 条历史记忆标为 stale，并创建 "
                f"{len(stale[-1].review_work_item_ids)} 个复核项；当前有 {sum(item.status == 'verified' for item in memories)} 条 verified 记忆。"
            ),
            evidence_level="verified",
            evidence_refs=[stale[-1].stale_propagation_id, *stale[-1].review_work_item_ids],
        ))
    gate = (
        "candidate_behavior_supported"
        if all(pair[1].status == "failed" and pair[3].status == "passed" for pair in paired)
        else "not_supported"
    )
    metrics: dict[str, int | float | str] = {
        "paired_trials": len(paired),
        "baseline_verification_passes": sum(item.status == "passed" for item in baseline_verifications),
        "candidate_verification_passes": sum(item.status == "passed" for item in candidate_verifications),
        "control_plane_model_calls": len(run.model_call_ids),
        "control_plane_tool_calls": len(run.tool_call_ids),
        "control_plane_input_tokens": sum(item.input_tokens for item in usages),
        "control_plane_output_tokens": sum(item.output_tokens for item in usages),
        "successful_run_cost_usd": round(run.spent_cost_usd, 12),
        "case_binding_cost_before_report_usd": round(binding_spend, 12),
        "approved_control_plane_and_report_budget_usd": binding.batch_budget_usd,
        "budget_remaining_before_report_usd": round(binding.batch_budget_usd - binding_spend, 12),
        "skill_ablation_runs": len(skill_verifications),
        "skill_ablation_passes": sum(item.status == "passed" for item in skill_verifications),
    }
    evidence_refs = [
        comparison.evolution_comparison_id,
        *(trial.evolution_trial_id for pair in paired for trial in (pair[0], pair[2])),
        run.evolution_agent_run_id,
        *(item.skill_ablation_verification_id for item in skill_verifications),
    ]
    manifest = ReportManifest(
        project_id=project_id,
        evolution_case_id=evolution_case_id,
        comparison_id=comparison.evolution_comparison_id,
        baseline_revision_id=case.baseline_revision_id,
        candidate_revision_id=case.candidate_revision_id,
        environment_fingerprint=next(iter(environment_fingerprints)),
        control_plane_run_id=run.evolution_agent_run_id,
        facts=facts,
        metrics=metrics,
        evaluation_gate=gate,
        evidence_refs=evidence_refs,
        manifest_fingerprint="0" * 64,
    )
    manifest = manifest.model_copy(update={"manifest_fingerprint": _manifest_fingerprint(manifest)})
    store.save("report_manifest", manifest.report_manifest_id, project_id, manifest)
    return manifest


class ReportNarrativeAdapter:
    def __init__(self, store: Store, manifest: ReportManifest, output_dir: Path) -> None:
        self.store = store
        self.manifest = manifest
        self.output_dir = output_dir
        self.read = False

    def tool_specs(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_report_manifest",
                    "description": "Read the immutable report facts, metrics, evidence references, and evaluation Gate.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_report_narrative",
                    "description": "提交简体中文 inferred 阅读层；每个章节必须引用不可变 Manifest fact ID。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "executive_summary": {"type": "string"},
                            "sections": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "heading": {"type": "string"},
                                        "fact_refs": {"type": "array", "items": {"type": "string"}},
                                        "interpretation": {"type": "string"},
                                    },
                                    "required": ["heading", "fact_refs", "interpretation"],
                                    "additionalProperties": False,
                                },
                            },
                            "skill_ablation_summary": {"type": "string"},
                            "skill_dimensions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "dimension": {
                                            "type": "string",
                                            "enum": ["trigger", "trace", "deliverable", "boundary"],
                                        },
                                        "fact_refs": {"type": "array", "items": {"type": "string"}},
                                        "conclusion": {"type": "string"},
                                    },
                                    "required": ["dimension", "fact_refs", "conclusion"],
                                    "additionalProperties": False,
                                },
                            },
                            "limitations": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["title", "executive_summary", "sections", "limitations"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def execute(self, name: str, arguments: dict[str, object]) -> TargetObservation:
        if name == "read_report_manifest":
            if arguments:
                raise ValueError("read_report_manifest accepts no arguments")
            self.read = True
            return TargetObservation({"locale": "zh-CN", "report_manifest": self.manifest.model_dump()})
        if name != "submit_report_narrative":
            raise ValueError(f"Unknown report tool: {name}")
        if not self.read:
            raise ValueError("The immutable ReportManifest must be read before narrative submission")
        sections = arguments.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ValueError("sections must be a non-empty list")
        known = {item.fact_id for item in self.manifest.facts}
        observed: set[str] = set()
        for section in sections:
            if not isinstance(section, dict):
                raise ValueError("Each section must be an object")
            refs = section.get("fact_refs")
            if not isinstance(refs, list) or not refs or not all(isinstance(item, str) for item in refs):
                raise ValueError("Each narrative section must cite manifest fact IDs")
            if not set(refs) <= known:
                raise ValueError("Narrative cited a fact outside the immutable ReportManifest")
            observed.update(refs)
        required_categories = {"baseline_verifier", "candidate_verifier", "pair_equivalence"}
        required_refs = {item.fact_id for item in self.manifest.facts if item.category in required_categories}
        if not required_refs <= observed:
            raise ValueError("Narrative must cover baseline, candidate, and pair-equivalence facts")
        skill_dimensions = arguments.get("skill_dimensions", [])
        skill_categories = {category for category in _SKILL_DIMENSIONS.values() if any(
            fact.category == category for fact in self.manifest.facts
        )}
        skill_summary = arguments.get("skill_ablation_summary")
        if skill_categories:
            if not isinstance(skill_summary, str) or not skill_summary.strip():
                raise ValueError("Skill ablation report requires a concise plain-language summary")
            if not isinstance(skill_dimensions, list) or {item.get("dimension") for item in skill_dimensions if isinstance(item, dict)} != set(_SKILL_DIMENSIONS):
                raise ValueError("Skill ablation report requires exactly trigger, trace, deliverable, and boundary conclusions")
            for item in skill_dimensions:
                if not isinstance(item, dict):
                    raise ValueError("Each Skill dimension conclusion must be an object")
                dimension = item.get("dimension")
                refs = item.get("fact_refs")
                conclusion = item.get("conclusion")
                if (
                    not isinstance(dimension, str)
                    or not isinstance(refs, list)
                    or not refs
                    or not all(isinstance(ref, str) for ref in refs)
                    or not isinstance(conclusion, str)
                    or not conclusion.strip()
                ):
                    raise ValueError("Each Skill dimension conclusion requires a cited non-empty conclusion")
                required_category = _SKILL_DIMENSIONS[dimension]
                if not any(fact.category == required_category and fact.fact_id in refs for fact in self.manifest.facts):
                    raise ValueError("Each Skill dimension conclusion must cite its verified evidence fact")
        elif skill_dimensions or skill_summary:
            raise ValueError("Skill ablation narrative is only valid when immutable Skill facts are present")
        text_values = [str(arguments.get("title", "")), str(arguments.get("executive_summary", ""))]
        text_values.extend(str(section.get("heading", "")) for section in sections if isinstance(section, dict))
        text_values.extend(str(section.get("interpretation", "")) for section in sections if isinstance(section, dict))
        if isinstance(skill_summary, str):
            text_values.append(skill_summary)
        text_values.extend(str(item.get("conclusion", "")) for item in skill_dimensions if isinstance(item, dict))
        limitations = arguments.get("limitations")
        if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
            raise ValueError("limitations must be a list of strings")
        text_values.extend(limitations)
        if not all(_contains_cjk(value) for value in text_values):
            raise ValueError("zh-CN report narrative must use Simplified Chinese")
        return TargetObservation({"report_narrative": arguments}, terminal=True)

    def restore(self, observations: list[dict[str, object]]) -> None:
        self.read = any(item.get("report_manifest") for item in observations)

    def complete_terminal(self, project_id: str, run_id: str, payload: dict[str, object]) -> TerminalArtifact:
        raw = payload.get("report_narrative")
        if not isinstance(raw, dict):
            raise ValueError("Terminal report observation is invalid")
        sections = [ReportNarrativeSection.model_validate(item) for item in raw["sections"]]
        skill_dimensions = [SkillDimensionNarrative.model_validate(item) for item in raw.get("skill_dimensions", [])]
        fact_refs = list(dict.fromkeys(ref for section in sections for ref in section.fact_refs))
        narrative = ReportNarrative(
            project_id=project_id,
            evolution_case_id=self.manifest.evolution_case_id,
            report_manifest_id=self.manifest.report_manifest_id,
            report_agent_run_id=run_id,
            status="completed",
            locale="zh-CN",
            title=str(raw["title"]),
            executive_summary=str(raw["executive_summary"]),
            sections=sections,
            skill_ablation_summary=str(raw["skill_ablation_summary"]).strip()
            if isinstance(raw.get("skill_ablation_summary"), str) else None,
            skill_dimensions=skill_dimensions,
            limitations=[str(item) for item in raw["limitations"]],
            fact_refs=fact_refs,
            evaluation_gate_snapshot=self.manifest.evaluation_gate,
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        html_path = self.output_dir / f"{narrative.report_narrative_id}.html"
        html_path.write_text(render_report_html(self.manifest, narrative), encoding="utf-8")
        html_ref = f"file:{html_path};sha256:{hashlib.sha256(html_path.read_bytes()).hexdigest()}"
        narrative = narrative.model_copy(update={"html_evidence_ref": html_ref})
        self.store.save("report_narrative", narrative.report_narrative_id, project_id, narrative)
        return TerminalArtifact("report_narrative", narrative.report_narrative_id, "report_narrative_completed")

def record_blocked_report(store: Store, manifest: ReportManifest, report_run: EvolutionAgentRun) -> ReportNarrative:
    narrative = ReportNarrative(
        project_id=manifest.project_id,
        evolution_case_id=manifest.evolution_case_id,
        report_manifest_id=manifest.report_manifest_id,
        report_agent_run_id=report_run.evolution_agent_run_id,
        status="blocked",
        locale="zh-CN",
        title="正式报告叙事生成受阻",
        executive_summary="不可变 ReportManifest 仍可审查，但所要求的真实 API 报告 Agent 未完成，因此未生成正式 HTML 报告。",
        evaluation_gate_snapshot=manifest.evaluation_gate,
        terminal_reason=report_run.terminal_reason or report_run.status,
    )
    store.save("report_narrative", narrative.report_narrative_id, manifest.project_id, narrative)
    return narrative


def _render_report_html_legacy(manifest: ReportManifest, narrative: ReportNarrative, *, sample: bool = False) -> str:
    facts = {item.fact_id: item for item in manifest.facts}
    fact_cards = []
    indexed_facts: set[str] = set()
    sections = []
    for section in narrative.sections:
        items = []
        for ref in section.fact_refs:
            fact = facts[ref]
            label = _CATEGORY_LABELS.get(fact.category, fact.category)
            items.append(
                "<article class='evidence-item'>"
                f"<div><span class='evidence-kind'>{html.escape(label)}</span>"
                f"<p>{html.escape(fact.statement)}</p></div>"
                f"<code>{html.escape(fact.fact_id)}</code></article>"
            )
            if fact.fact_id not in indexed_facts:
                fact_cards.append(
                    f"<li><span>{html.escape(label)}</span><strong>{html.escape(fact.evidence_level)}</strong>"
                    f"<code>{html.escape(fact.fact_id)}</code></li>"
                )
                indexed_facts.add(fact.fact_id)
        sections.append(
            "<section class='panel narrative-panel'>"
            f"<div class='section-heading'><span>推断解释</span><h2>{html.escape(section.heading)}</h2></div>"
            f"<div class='evidence-list'>{''.join(items)}</div>"
            f"<p class='interpretation'>{html.escape(section.interpretation)}</p></section>"
        )
    limitations = "".join(f"<li>{html.escape(item)}</li>" for item in narrative.limitations)
    metrics = "".join(
        "<div class='metric'>"
        f"<span>{html.escape(_METRIC_LABELS.get(str(key), str(key)))}</span>"
        f"<strong>{html.escape(str(value))}</strong></div>"
        for key, value in manifest.metrics.items()
    )
    gate_label = _GATE_LABELS[manifest.evaluation_gate]
    gate_class = "supported" if manifest.evaluation_gate == "candidate_behavior_supported" else "blocked"
    sample_banner = (
        "<div class='sample-banner'>界面输出示例 · 叙事层用于展示 zh-CN 样式，不构成新的 Provider 运行或发布结论</div>"
        if sample else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{html.escape(narrative.title)}</title>
  <style>
    :root{{--bg:#070b14;--surface:#0d1423;--surface-2:#111a2d;--line:#24314a;--text:#f3f6fb;--muted:#91a0b8;--accent:#4f7cff;--good:#42d6a4;--warn:#ffb454;--bad:#ff5f70;--radius:14px}}
    *{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 76% 0,#12213f 0,transparent 32%),var(--bg);color:var(--text);font:15px/1.6 "Segoe UI","Microsoft YaHei UI",system-ui,sans-serif}}
    a{{color:inherit}} code{{font-family:"Cascadia Code","SFMono-Regular",monospace;font-size:12px;color:#9eb7ff;overflow-wrap:anywhere}}
    .shell{{max-width:1440px;margin:auto;padding:30px}} .topbar{{display:flex;align-items:center;justify-content:space-between;padding:0 0 22px;border-bottom:1px solid var(--line)}}
    .brand{{display:flex;gap:12px;align-items:center;font-weight:700}} .mark{{display:grid;place-items:center;width:38px;height:38px;border:1px solid #6688ff;border-radius:12px;color:#a9bbff;background:#101a33}}
    .meta{{color:var(--muted);font-size:13px}} .breadcrumb{{margin:26px 0 8px;color:var(--muted)}} h1{{font-size:34px;line-height:1.2;margin:0 0 9px;letter-spacing:-.02em}} h2{{font-size:18px;margin:4px 0 0}}
    .lede{{max-width:800px;color:#c4cee0;margin:0}} .sample-banner{{margin:18px 0;padding:10px 14px;border:1px solid #6c5827;background:#211b0e;color:#ffd78b;border-radius:var(--radius)}}
    .hero{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr);gap:18px;margin-top:22px}} .panel{{background:linear-gradient(145deg,rgba(18,28,48,.96),rgba(11,17,30,.96));border:1px solid var(--line);border-radius:var(--radius);padding:22px}}
    .decision{{position:relative;overflow:hidden}} .decision:after{{content:"";position:absolute;inset:auto -90px -110px auto;width:280px;height:280px;border-radius:50%;background:radial-gradient(circle,rgba(79,124,255,.18),transparent 68%)}}
    .eyebrow,.section-heading span{{display:block;color:#8fa1be;font-size:12px;letter-spacing:.08em;text-transform:uppercase}} .gate{{font-size:31px;line-height:1.15;margin:14px 0 8px;font-weight:800}} .gate.supported{{color:var(--good)}} .gate.blocked{{color:var(--bad)}}
    .decision-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:22px}} .decision-grid div{{padding-top:12px;border-top:1px solid var(--line)}} .decision-grid span{{display:block;color:var(--muted);font-size:12px}} .decision-grid strong{{font-size:14px}}
    .chain ol{{list-style:none;margin:16px 0 0;padding:0;display:grid;gap:10px}} .chain li{{display:grid;grid-template-columns:92px 1fr auto;gap:12px;align-items:center;padding:11px 0;border-bottom:1px solid rgba(36,49,74,.7)}} .chain li:last-child{{border-bottom:0}} .chain span{{color:var(--muted)}}
    .metrics{{display:grid;grid-template-columns:repeat(4,minmax(145px,1fr));gap:12px;margin:18px 0}} .metric{{min-height:92px;padding:16px;border:1px solid var(--line);border-radius:var(--radius);background:#0b1220}} .metric span{{display:block;color:var(--muted);font-size:12px}} .metric strong{{display:block;margin-top:12px;font-size:19px;color:#e6ecff}}
    .content{{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px;align-items:start}} .narratives{{display:grid;gap:18px}} .section-heading{{margin-bottom:16px}} .evidence-list{{display:grid;gap:8px}} .evidence-item{{display:grid;grid-template-columns:minmax(0,1fr) 190px;gap:14px;align-items:center;padding:13px 14px;border:1px solid #2a3957;border-radius:10px;background:#0a1120}} .evidence-item p{{margin:3px 0 0;color:#c8d2e4}} .evidence-kind{{font-size:12px;color:#aebeff}}
    .interpretation{{margin:14px 0 0;padding:14px 16px;border-left:3px solid var(--accent);background:#0a1020;color:#dfe6f4}} .sidebar{{display:grid;gap:18px;position:sticky;top:18px}} .fact-index ul,.limits ul{{list-style:none;margin:12px 0 0;padding:0}} .fact-index li{{display:grid;grid-template-columns:1fr auto;gap:4px 10px;padding:11px 0;border-bottom:1px solid var(--line)}} .fact-index li code{{grid-column:1/-1}} .fact-index li strong{{color:var(--good);font-size:12px}} .limits li{{position:relative;padding:9px 0 9px 18px;color:#c6d0e2}} .limits li:before{{content:"";position:absolute;left:0;top:18px;width:6px;height:6px;border-radius:50%;background:var(--warn)}}
    .technical{{margin-top:18px;display:grid;grid-template-columns:1fr 1fr;gap:12px}} .technical div{{padding:12px 0;border-top:1px solid var(--line)}} .technical span{{display:block;color:var(--muted);font-size:12px}} footer{{margin-top:22px;padding:18px 0;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}}
    @media(max-width:980px){{.hero,.content{{grid-template-columns:1fr}}.sidebar{{position:static}}.metrics{{grid-template-columns:repeat(2,1fr)}}}} @media(max-width:620px){{.shell{{padding:18px}}.topbar{{align-items:flex-start;gap:12px}}.hero{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr}}.decision-grid{{grid-template-columns:1fr}}.evidence-item{{grid-template-columns:1fr}}.chain li{{grid-template-columns:1fr}}.technical{{grid-template-columns:1fr}}}}
    @media print{{body{{background:#fff;color:#111}}.panel,.metric,.evidence-item{{background:#fff;color:#111;border-color:#bbb}}.sidebar{{position:static}}}}
  </style>
</head>
<body><main class="shell">
  <header class="topbar"><div class="brand"><div class="mark">AIG</div><div>Agent Iteration Guard<br><span class="meta">可审计版本迭代评估</span></div></div><div class="meta">报告语言 · 简体中文</div></header>
  <div class="breadcrumb">报告 / Evolution Case / {html.escape(manifest.evolution_case_id)}</div>
  <h1>{html.escape(narrative.title)}</h1><p class="lede">{html.escape(narrative.executive_summary)}</p>{sample_banner}
  <div class="hero">
    <section class="panel decision"><span class="eyebrow">Evaluation Gate</span><div class="gate {gate_class}">{html.escape(gate_label)}</div><p>该 Gate 只表达当前批准案例的行为证据，不等于发布批准。</p><div class="decision-grid"><div><span>发布状态</span><strong>未进行发布评估</strong></div><div><span>报告状态</span><strong>{'已完成' if narrative.status == 'completed' else '受阻'}</strong></div><div><span>基线 Revision</span><code>{html.escape(manifest.baseline_revision_id)}</code></div><div><span>候选 Revision</span><code>{html.escape(manifest.candidate_revision_id)}</code></div></div></section>
    <section class="panel chain"><span class="eyebrow">Evidence Chain</span><h2>证据如何形成结论</h2><ol><li><span>变更</span><strong>Revision pair</strong><code>{html.escape(manifest.comparison_id)}</code></li><li><span>执行</span><strong>真实 control-plane Agent</strong><code>{html.escape(manifest.control_plane_run_id)}</code></li><li><span>验证</span><strong>独立 Verifier</strong><code>immutable facts</code></li><li><span>结论</span><strong>{html.escape(gate_label)}</strong><code>{html.escape(manifest.evaluation_gate)}</code></li></ol></section>
  </div>
  <section class="metrics">{metrics}</section>
  <div class="content"><div class="narratives">{''.join(sections)}</div><aside class="sidebar"><section class="panel fact-index"><span class="eyebrow">Fact Index</span><h2>不可变事实索引</h2><ul>{''.join(fact_cards)}</ul></section><section class="panel limits"><span class="eyebrow">Scope</span><h2>限制与适用边界</h2><ul>{limitations}</ul></section></aside></div>
  <section class="panel technical"><div><span>Manifest 指纹</span><code>{html.escape(manifest.manifest_fingerprint)}</code></div><div><span>环境指纹</span><code>{html.escape(manifest.environment_fingerprint)}</code></div><div><span>ReportManifest</span><code>{html.escape(manifest.report_manifest_id)}</code></div><div><span>ReportNarrative</span><code>{html.escape(narrative.report_narrative_id)}</code></div></section>
  <footer>事实层来自不可变 ReportManifest。中文叙事层为 inferred，仅帮助阅读，不能改变 Gate、指标或发布状态。</footer>
</main></body></html>"""


def render_report_html(manifest: ReportManifest, narrative: ReportNarrative, *, sample: bool = False) -> str:
    """Render the public report without exposing run, task, or artifact identifiers."""
    facts = {item.fact_id: item for item in manifest.facts}
    detail_sections = []
    for section in narrative.sections:
        evidence = "".join(
            "<li>"
            f"<span>{html.escape(_CATEGORY_LABELS.get(facts[ref].category, facts[ref].category))}</span>"
            f"{html.escape(facts[ref].statement)}</li>"
            for ref in section.fact_refs
        )
        detail_sections.append(
            "<section class='card detail'><h2>"
            f"{html.escape(section.heading)}</h2><p>{html.escape(section.interpretation)}</p>"
            f"<ul>{evidence}</ul></section>"
        )
    skill_cards = []
    for dimension in narrative.skill_dimensions:
        evidence = "".join(
            f"<li>{html.escape(facts[ref].statement)}</li>" for ref in dimension.fact_refs
        )
        skill_cards.append(
            "<article class='skill-card'><span>"
            f"{html.escape(_SKILL_DIMENSION_LABELS[dimension.dimension])}</span>"
            f"<h3>{html.escape(dimension.conclusion)}</h3><ul>{evidence}</ul></article>"
        )
    skill_block = ""
    if narrative.skill_ablation_summary:
        skill_block = (
            "<section class='card skill-summary'><p class='eyebrow'>Tool / Skill 有效性结论</p>"
            f"<h2>{html.escape(narrative.skill_ablation_summary)}</h2>"
            f"<div class='skill-grid'>{''.join(skill_cards)}</div></section>"
        )
    metrics = "".join(
        "<div class='metric'><span>"
        f"{html.escape(_METRIC_LABELS.get(str(key), str(key)))}</span>"
        f"<strong>{html.escape(str(value))}</strong></div>"
        for key, value in manifest.metrics.items()
        if key not in {"control_plane_input_tokens", "control_plane_output_tokens"}
    )
    limitations = "".join(f"<li>{html.escape(item)}</li>" for item in narrative.limitations)
    gate_label = _GATE_LABELS[manifest.evaluation_gate]
    gate_class = "good" if manifest.evaluation_gate == "candidate_behavior_supported" else "caution"
    sample_banner = "<p class='sample'>这是展示样式，不代表新的实验结论。</p>" if sample else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(narrative.title)}</title><style>
:root{{--ink:#1a2433;--muted:#637085;--paper:#f6f8fc;--card:#fff;--line:#dfe5ef;--blue:#2458d3;--good:#13795b;--warn:#9a5b00}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 "Microsoft YaHei",system-ui,sans-serif}}
main{{max-width:1080px;margin:auto;padding:42px 24px 64px}} h1{{font-size:36px;line-height:1.22;margin:10px 0}} h2{{font-size:21px;line-height:1.45;margin:0 0 12px}} h3{{font-size:17px;line-height:1.5;margin:7px 0 10px}}
.brand,.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.04em}} .brand{{font-size:14px}} .lede{{max-width:820px;color:var(--muted);font-size:18px;margin:0 0 26px}} .sample{{color:var(--warn)}}
.hero,.skill-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;margin:16px 0;box-shadow:0 4px 14px rgba(24,42,72,.04)}}
.verdict{{font-size:28px;font-weight:800;margin:8px 0;color:var(--good)}} .verdict.caution{{color:var(--warn)}} .hero p{{margin:6px 0;color:var(--muted)}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}} .metric{{background:#eef3ff;border-radius:12px;padding:14px}} .metric span{{display:block;color:var(--muted);font-size:13px}} .metric strong{{display:block;font-size:20px;margin-top:8px}}
.skill-summary h2{{font-size:22px}} .skill-card{{border:1px solid #cfd9f1;border-radius:12px;padding:16px;background:#fbfcff}} .skill-card>span,.detail li span{{display:block;color:var(--blue);font-size:13px;font-weight:700}}
ul{{margin:0;padding-left:20px}} li{{margin:8px 0}} .detail>p{{font-weight:600;margin:0 0 13px}} .detail ul{{color:var(--muted)}} footer{{color:var(--muted);font-size:13px;margin-top:28px}}
@media(max-width:720px){{main{{padding:28px 16px}}.hero,.skill-grid,.metrics{{grid-template-columns:1fr}}h1{{font-size:30px}}}}
</style></head><body><main>
<div class="brand">Agent Iteration Guard · Tool / Skill 评测报告</div><h1>{html.escape(narrative.title)}</h1><p class="lede">{html.escape(narrative.executive_summary)}</p>{sample_banner}
<div class="hero"><section class="card"><p class="eyebrow">本次结论</p><div class="verdict {gate_class}">{html.escape(gate_label)}</div><p>结论来自相同条件下的对照运行与独立核验，不等同于发布批准。</p></section><section class="card"><p class="eyebrow">我们如何得到结论</p><h2>真实运行、独立核验、再用自然语言解释</h2><p>系统记录触发、执行过程、交付物和边界行为；DeepSeek 只负责把已核验的结果讲清楚。</p></section></div>
<section class="metrics">{metrics}</section>{skill_block}<section class="card"><p class="eyebrow">过程与证据</p><h2>哪些事实支持了本次结论</h2>{''.join(detail_sections)}</section>
<section class="card"><p class="eyebrow">适用范围</p><ul>{limitations}</ul></section><footer>报告隐去运行与任务编号；底层不可变证据仍由本地评测记录保存并可审计。</footer>
</main></body></html>"""
