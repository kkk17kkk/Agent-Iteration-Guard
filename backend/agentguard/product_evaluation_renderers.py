"""Presentation projections for ProductEvaluationReport.

The renderer deliberately consumes the same normalized view model as the web
Preview.  Report generation remains a presentation concern: it does not alter
evaluation, runner, or immutable evidence data.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .assets import asset_path
from .product_evaluation_report import ProductEvaluationReport
from .product_report_template import ProductReportTemplate, default_product_report_template
from .report_view_model import NormalizedReport, normalize_product_evaluation_report


_HTML_STYLE_OVERRIDES = """
.decision { grid-template-columns: minmax(0, 1fr) minmax(340px, .78fr); gap: 28px; align-items: stretch; min-height: 0; }
.decision > div { min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.decision-status { color: var(--good); }
.decision-status.supported { color: var(--good); }
.decision-copy, .decision-evidence-note { max-width: none; overflow-wrap: anywhere; }
.decision-evidence-note { margin-top: 13px; padding: 10px 12px; border-left: 3px solid var(--warn); border-radius: 7px; color: #f3d59b; background: rgba(244, 189, 89, .08); font-size: 14px; line-height: 1.65; }
.decision-checks { min-width: 0; align-self: stretch; display: grid; align-content: center; gap: 7px; }
.decision-checks li { display: grid; grid-template-columns: minmax(140px, .7fr) minmax(0, .55fr); gap: 12px; align-items: center; min-width: 0; padding: 9px 0; overflow-wrap: anywhere; }
.context-table { table-layout: fixed; }
.context-table th, .context-table td { padding: 15px 14px; line-height: 1.85; overflow-wrap: anywhere; }
.context-table td { font-size: 15px; }
.finding-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
.finding-card { min-width: 0; padding: 15px 16px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-2); }
.finding-card strong { display: block; font-size: 16px; line-height: 1.45; }
.finding-card p { margin: 8px 0 0; color: var(--body); font-size: 15px; line-height: 1.7; overflow-wrap: anywhere; }
.report-summary-section { background: linear-gradient(135deg, #101b39, #15152e); border-color: #30416a; --summary-callout-bg: linear-gradient(135deg, rgba(21, 35, 75, .92), rgba(37, 27, 70, .76)); }
.report-summary-section .summary-callout,
.report-summary-section .summary-recommendation { background: var(--summary-callout-bg); border-color: rgba(115, 139, 255, .68); }
.report-summary-section .finding-card,
.report-summary-section .finding-capability_value,
.report-summary-section .finding-capability_loss,
.report-summary-section .finding-replacement_risk,
.report-summary-section .finding-stability { background: var(--surface-2); border-color: var(--line); }
.summary-recommendation { margin-top: 25px; padding: 14px 16px; border-left: 3px solid var(--violet); border-radius: 8px; background: rgba(141, 108, 255, .13); color: var(--text); }
.summary-followup { margin-top: 14px; }
.section-head { display: block; }
.section-head .eyebrow, .section-head h2 { display: block; }
.section-head h2 { clear: both; margin-top: 6px; }
@media(max-width:1100px) { .decision { grid-template-columns: 1fr; } }
@media(max-width:700px) { .finding-grid { grid-template-columns: 1fr; } }
"""


def render_product_evaluation_markdown(
    report: ProductEvaluationReport,
    template: ProductReportTemplate | None = None,
    *,
    project_context: Mapping[str, object] | None = None,
    gate: Mapping[str, object] | None = None,
) -> str:
    """Render the portable report from the shared presentation view model."""

    template = template or default_product_report_template()
    view = normalize_product_evaluation_report(report, project_context=project_context, gate=gate)
    sections = {section.section_id: section for section in template.sections}
    labels = template.labels
    lines = [
        f"# {view.title}",
        "",
        "## 项目上下文 / Project Context",
        "",
        f"- 项目：{view.project.project_name}",
        f"- 用途：{view.project.purpose}",
        f"- Baseline：{view.project.baseline}",
        f"- Candidate：{view.project.candidate}",
        f"- Runtime：{view.project.runtime}",
        "",
        f"## {sections['capability_overview'].eyebrow}",
        f"### {sections['capability_overview'].title}",
        "",
    ]
    overview = view.capability_overview
    lines.extend(_markdown_fields(overview, labels, ("product_role", "why_it_exists", "user_problem", "boundary")))
    lines.extend([f"**{labels['ideal_behavior']}**：", *[f"- {item}" for item in overview.get("ideal_behavior", [])], ""])

    lines.extend([f"## {sections['evaluation_context'].eyebrow}", f"### {sections['evaluation_context'].title}", "", "| 项目 | 内容 |", "| --- | --- |"])
    lines.extend(f"| {item.get('label', '')} | {item.get('value', '')} |" for item in view.evaluation_context.get("items", []))
    lines.append("")

    summary = view.summary
    lines.extend([
        f"## {sections['executive_summary'].eyebrow}",
        f"### {sections['executive_summary'].title}",
        "",
        f"**{labels['final_conclusion']}**：{summary.get('final_conclusion', '')}",
        "",
        f"**{labels['main_findings']}**：",
        *[f"- **{item.get('title', '')}**：{item.get('statement', '')}" for item in summary.get("main_findings", [])],
        "",
        f"**{labels['product_recommendation']}**：{summary.get('product_recommendation', '')}",
        "",
        f"**{labels['follow_up_priorities']}**：",
        *[f"- {item}" for item in summary.get("follow_up_priorities", [])],
        "",
        f"## {sections['evaluation_dimensions'].eyebrow}",
        f"### {sections['evaluation_dimensions'].title}",
        "",
    ])
    for item in view.dimensions:
        conclusion = str(item.get("conclusion", "")).rstrip("。")
        lines.extend([f"### {_dimension_label(template, item.get('dimension', ''))}", "", f"{conclusion}。{item.get('explanation', '')}", ""])

    experiments = view.experiments
    lines.extend([f"## {sections['experiment_overview'].eyebrow}", f"### {sections['experiment_overview'].title}", "", experiments.get("summary", ""), ""])
    lines.extend(_suite_markdown(view.evaluation_suite))
    for item in experiments.get("questions", []):
        lines.extend([f"### {item.get('name', '')}", "", f"**{labels['experiment_question']}**：{item.get('question', '')}", "", f"**{labels['experiment_purpose_short']}**：{item.get('purpose', '')}", ""])

    lines.extend([f"## {sections['experiment_analysis'].eyebrow}", f"### {sections['experiment_analysis'].title}", ""])
    for item in experiments.get("analysis", []):
        lines.extend(_markdown_analysis(item, labels))
    interaction = getattr(report, "interaction_analysis", None)
    if interaction is not None:
        lines.extend(_markdown_interaction(interaction))
    if report.root_cause_findings:
        lines.extend(["### Recurring Failure Patterns and Analyst RCA", ""])
        for finding in report.root_cause_findings:
            lines.extend([
                f"#### {finding.observed_failure_type} · {finding.root_cause_category}", "",
                f"- Support: {finding.frequency} occurrences / {finding.affected_trial_count} affected trials / {finding.affected_scenario_count} scenarios",
                f"- Conditions: {', '.join(finding.affected_conditions)}",
                f"- Stability: {finding.stability}",
                f"- Confidence: {finding.root_cause_confidence}",
                f"- Analyst hypothesis: {finding.analyst_hypothesis}",
                f"- Evidence refs: {', '.join(finding.evidence_refs)}", "",
            ])

    stability = view.scenario_stability
    lines.extend([f"## {sections['scenario_stability'].eyebrow}", f"### {sections['scenario_stability'].title}", "", stability.get("summary", ""), "", f"**{labels['scenario_conclusion']}**：{stability.get('coverage_conclusion', '')}", ""])
    for item in stability.get("scenarios", []):
        lines.extend([f"### {item.get('name', '')}{_scenario_id_markdown(item.get('scenario_id'))}", "", f"**{labels['scenario_user']}**：{item.get('user_prompt', '')}", "", f"**{labels['scenario_purpose']}**：{item.get('purpose', '')}", "", f"**{labels['scenario_observation']}**：{item.get('observation', '')}", "", f"**{labels['scenario_result']}**：{item.get('result', '')}", ""])

    lines.extend([f"## {sections['product_impact'].eyebrow}", f"### {sections['product_impact'].title}", "", f"**{labels['affected_user_journey']}**：{view.impact.get('affected_user_journey', '')}", "", view.impact.get("user_consequence", ""), "", *[f"- {item.get('product_meaning', '')}" for item in view.impact.get("findings", [])], ""])
    lines.extend([f"## {sections['recommendation'].eyebrow}", f"### {sections['recommendation'].title}", ""])
    for item in view.recommendations:
        lines.extend([f"### {item.get('target', '')}（{item.get('priority', '')}）", "", item.get("action", ""), "", f"**{labels['reasoning']}**：{item.get('reasoning', '')}", "", f"**{labels['next_step']}**：{'；'.join(item.get('validation_plan', []))}", ""])
    lines.extend([f"## {sections['limitations'].eyebrow}", f"### {sections['limitations'].title}", "", *[f"- {item.get('statement', '')}" for item in view.limitations], ""])

    evidence = view.evidence_bundle
    lines.extend([f"## {sections['evidence'].eyebrow}", f"### {sections['evidence'].title}", "", "Product Evidence / Experiment Evidence / Technical Evidence", "", f"- 状态：{evidence.get('status', '')}", f"- 已验证条件：{view.metrics.get('verified_count', 0)}", f"- 通过：{view.metrics.get('passed_count', 0)}", f"- 失败：{view.metrics.get('failed_count', 0)}", f"- 实验条件：{view.metrics.get('condition_count', 0)}", f"- 成本：{_cost_text(view.metrics.get('cost_usd'))}", ""])
    for condition in evidence.get("conditions", []):
        lines.extend([f"<details><summary>{condition.get('label', '')} · {condition.get('kind_label', condition.get('kind', ''))} · {_status_label_zh(condition.get('status'))}</summary>", "", f"证据引用：{', '.join(condition.get('evidence_refs', []))}", "", "</details>", ""])

    lines.extend([f"## {sections['technical_metadata'].eyebrow}", f"### {sections['technical_metadata'].title}", ""])
    for key, value in view.technical_metadata.items():
        lines.append(f"- {key}：{_stringify(value)}")
    lines.extend(["", "技术记录、事实与补充证据保留在可展开的 HTML 详情中；首屏不直接倾倒原始 JSON。", ""])
    return "\n".join(lines)


def render_product_evaluation_html(
    report: ProductEvaluationReport,
    template: ProductReportTemplate | None = None,
    *,
    project_context: Mapping[str, object] | None = None,
    gate: Mapping[str, object] | None = None,
) -> str:
    """Render the same normalized report as an independent archive document."""

    template = template or default_product_report_template()
    view = normalize_product_evaluation_report(report, project_context=project_context, gate=gate)
    sections = {section.section_id: section for section in template.sections}
    esc = _esc
    logo = _logo_svg()
    status_class = _status_class(view.decision.get("decision"))
    metrics = "".join(
        f"<div class='metric'><span>{esc(label)}</span><strong>{esc(value)}</strong></div>"
        for label, value in (
            ("报告状态", view.metrics.get("report_status")),
            ("Evidence", view.metrics.get("evidence_status")),
            ("实验总数", view.metrics.get("experiment_count")),
            ("Findings", view.metrics.get("findings_count")),
        )
    )
    sections_html = [
        _section(sections["capability_overview"], _overview_html(view.capability_overview, template, esc)),
        _section(sections["evaluation_context"], _context_html(view.evaluation_context, esc)),
        _section(sections["executive_summary"], _summary_html(view.summary, view.decision, labels=template.labels, esc=esc), class_name="report-summary-section"),
        _section(sections["evaluation_dimensions"], _dimensions_html(view.dimensions, template, esc)),
        _section(sections["experiment_overview"], _experiments_overview_html(view.experiments, esc) + _suite_html(view.evaluation_suite, esc)),
        _section(sections["experiment_analysis"], _analysis_html(view.experiments.get("analysis", []), template, esc)),
        _section(sections["scenario_stability"], _stability_html(view.scenario_stability, template, esc)),
        _section(sections["product_impact"], _impact_html(view.impact, esc)),
        _section(sections["recommendation"], _recommendations_html(view.recommendations, template.labels, esc)),
        _section(sections["limitations"], _limitations_html(view.limitations, esc)),
        _section(sections["evidence"], _evidence_html(view, esc)),
        _section(sections["technical_metadata"], _technical_html(view.technical_evidence, view.technical_metadata, esc)),
    ]
    interaction = getattr(report, "interaction_analysis", None)
    if interaction is not None:
        sections_html[5] += _interaction_html(interaction, esc)
    if report.root_cause_findings:
        sections_html[5] += _root_cause_html(report.root_cause_findings, esc)
    aside = (
        "<aside class='sidebar' aria-label='证据索引'>"
        "<div class='side-index'><span>Product Evidence</span><strong>产品证据</strong><p>来自报告的能力结果与影响。</p></div>"
        "<div class='side-index'><span>Experiment Evidence</span><strong>实验实证</strong><p>条件、场景与结果均可展开查看。</p></div>"
        "<div class='side-index'><span>Technical Evidence</span><strong>技术证据</strong><p>原始记录仅用于审计与追溯。</p></div>"
        f"{_technical_side_html(view.technical_evidence, esc)}</aside>"
    )
    html = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'>
<title>{esc(view.title)}</title>
<style>
:root{{--bg:#060914;--surface:#0d1220;--surface-2:#101625;--line:#222b40;--text:#f5f7fb;--body:#d8deea;--muted:#a9b2c3;--meta:#7f899d;--blue:#5d83ff;--violet:#8d6cff;--good:#35d6a3;--warn:#ffbf66;--bad:#ff6b79;--radius:14px}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 'Segoe UI','Microsoft YaHei UI',system-ui,sans-serif}}.shell{{max-width:1700px;margin:0 auto;padding:30px 32px 42px}}.topbar{{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding:0 0 20px}}.brand{{display:flex;align-items:center;gap:12px;font-size:16px;font-weight:700}}.brand-logo{{width:38px;height:38px;display:grid;place-items:center;overflow:hidden}}.brand-logo svg{{display:block;width:32px;height:32px;max-width:32px;max-height:32px}}.meta,.eyebrow,.metric span,.definition dt,.card-label{{color:var(--meta);font-size:12px;letter-spacing:.06em}}.meta{{letter-spacing:.03em}}.language{{color:var(--muted);font-size:13px}}.context{{margin-top:22px;padding:22px 24px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius)}}.context-head{{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}}.context-kicker{{margin:0 0 5px;color:var(--muted);font-size:13px}}h1{{margin:0;font-size:34px;line-height:1.2;letter-spacing:-.025em}}h2{{margin:5px 0 0;font-size:22px;line-height:1.3}}h3{{margin:4px 0 0;font-size:17px;line-height:1.4}}p{{margin:8px 0;color:var(--body)}}.context-purpose{{max-width:720px;margin:9px 0 0;color:var(--body)}}.context-crumb{{margin:14px 0 0;color:var(--blue)}}.context-meta{{display:grid;grid-template-columns:repeat(3,minmax(150px,1fr));gap:22px;min-width:510px;padding-top:12px}}.context-meta span{{display:block;color:var(--muted);font-size:13px}}.context-meta strong{{display:block;margin-top:4px;color:var(--text);font:600 13px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}}.decision{{display:grid;grid-template-columns:minmax(0,1fr) 150px;gap:20px;margin-top:16px;padding:22px 24px;background:linear-gradient(135deg,#101b39,#15152e);border:1px solid #30416a;border-radius:var(--radius)}}.decision-status{{font-size:42px;font-weight:800;line-height:1.05;color:var(--good)}}.decision-status.review{{color:var(--warn)}}.decision-status.blocked,.decision-status.failed{{color:var(--bad)}}.decision-rationale{{max-width:840px;color:var(--body)}}.decision-checks{{margin:0;padding:0;list-style:none;color:var(--muted);font-size:13px}}.decision-checks li{{padding:4px 0;border-bottom:1px solid rgba(255,255,255,.08)}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:16px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}}.metric{{min-height:92px;padding:17px 18px;border-right:1px solid var(--line)}}.metric:last-child{{border-right:0}}.metric strong{{display:block;margin-top:7px;font-size:21px;color:var(--text)}}.report-layout{{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:16px;margin-top:16px}}.narratives{{display:grid;gap:16px}}.report-section{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:22px 24px}}.section-head{{margin-bottom:16px}}.section-head .eyebrow{{display:block;text-transform:uppercase}}.lede{{color:var(--body);max-width:1000px}}.overview-grid,.dimension-grid,.experiment-map,.analysis-list,.scenario-list,.recommendation-list{{display:grid;gap:12px}}.overview-grid,.dimension-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.overview-item,.dimension,.map-item,.analysis-card,.scenario-card,.recommendation{{padding:15px;background:var(--surface-2);border:1px solid var(--line);border-radius:11px}}.overview-item p,.dimension p,.map-item p,.analysis-card p,.scenario-card p,.recommendation p{{margin:5px 0;color:var(--body)}}.ideal{{margin:12px 0 0;padding-left:22px;color:var(--body)}}.context-table{{width:100%;border-collapse:collapse}}.context-table th,.context-table td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}.context-table th{{width:190px;color:var(--muted);font-weight:600}}.context-table td{{color:var(--body)}}.summary-callout{{padding:16px;border-left:3px solid var(--blue);background:#0a1020;color:var(--body)}}.finding-list{{display:grid;gap:8px;margin:14px 0 0;padding:0;list-style:none}}.finding-list li{{padding:11px 13px;border:1px solid var(--line);border-radius:10px;color:var(--body)}}.finding-list strong{{color:var(--text)}}.map-item{{display:grid;grid-template-columns:30px 1fr;gap:12px}}.map-number{{display:grid;place-items:center;width:26px;height:26px;border-radius:50%;background:#17264a;color:#b8c7ff;font-weight:700}}.analysis-grid,.scenario-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin-top:12px}}.analysis-grid>div,.scenario-grid>div{{padding-top:9px;border-top:1px solid var(--line)}}.analysis-grid .wide,.scenario-grid .wide{{grid-column:1/-1}}.scenario-title,.scenario-heading{{display:flex;align-items:center;justify-content:space-between;gap:12px}}.scenario-title{{justify-content:flex-start}}.scenario-status{{color:var(--good);font-size:13px;font-weight:700}}.prompt{{padding:10px 12px;margin:12px 0;background:#0a1020;border-left:3px solid var(--blue);color:var(--body)}}.impact-list{{margin:12px 0 0;padding-left:20px;color:var(--body)}}.priority{{float:right;color:var(--warn);font-size:12px}}.next-step{{color:#b8c7ff!important}}.evidence-summary{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:15px}}.evidence-stat{{padding:13px;background:var(--surface-2);border:1px solid var(--line);border-radius:10px}}.evidence-stat strong{{display:block;margin-top:4px;font-size:18px}}details{{border-top:1px solid var(--line);padding:13px 0}}details:first-of-type{{border-top:0}}summary{{cursor:pointer;color:var(--text);font-weight:700}}.condition-meta{{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;color:var(--muted);font-size:13px}}.condition-status{{color:var(--good)}}.condition-status.failed{{color:var(--bad)}}.condition-status.review{{color:var(--warn)}}.evidence-detail{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}}.evidence-detail div{{padding:10px;background:#0a1020;border:1px solid var(--line);border-radius:9px}}.evidence-detail span{{display:block;color:var(--muted);font-size:12px}}.evidence-detail p{{margin:3px 0;overflow-wrap:anywhere}}.definition-list{{display:grid;grid-template-columns:180px 1fr;margin:0}}.definition-list dt,.definition-list dd{{padding:10px 0;border-bottom:1px solid var(--line)}}.definition-list dt{{font-weight:600}}.definition-list dd{{margin:0;color:var(--body);overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#080d18;border:1px solid var(--line);border-radius:9px;padding:12px;color:#dbe7ff;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}}.sidebar{{display:grid;gap:12px;align-content:start;position:sticky;top:18px}}.side-index{{padding:15px;background:var(--surface);border:1px solid var(--line);border-radius:12px}}.side-index span{{display:block;color:#aebeff;font-size:12px;letter-spacing:.06em}}.side-index strong{{display:block;margin-top:5px;font-size:16px}}.side-index p{{font-size:13px;color:var(--muted)}}footer{{margin-top:18px;padding-top:16px;border-top:1px solid var(--line);color:var(--meta);font-size:12px}}@media(max-width:1100px){{.context-head{{display:block}}.context-meta{{min-width:0;margin-top:20px}}.report-layout{{grid-template-columns:1fr}}.sidebar{{position:static;grid-template-columns:repeat(3,1fr)}}}}@media(max-width:700px){{.shell{{padding:18px}}.overview-grid,.dimension-grid,.analysis-grid,.scenario-grid,.evidence-summary,.sidebar{{grid-template-columns:1fr}}.metrics{{grid-template-columns:repeat(2,1fr)}}.metric:nth-child(2){{border-right:0}}.metric:nth-child(-n+2){{border-bottom:1px solid var(--line)}}.decision{{grid-template-columns:1fr}}.context-meta{{grid-template-columns:1fr}}.definition-list{{grid-template-columns:1fr}}.definition-list dd{{padding-top:0}}}}
</style></head><body><main class='shell'>
<header class='topbar'><div class='brand'><span class='brand-logo'>{logo}</span><div>Agent Iteration Guard<br><span class='meta'>Evidence-first · Local</span></div></div><div class='language'>简体中文 · 独立报告</div></header>
<section class='context'><div class='context-head'><div><p class='context-kicker'>项目详情 · PROJECT DETAIL</p><h1>{esc(view.project.project_name)}</h1><p class='context-purpose'>{esc(view.project.purpose)}</p><p class='context-crumb'>项目 / {esc(view.project.project_name)}</p></div><div class='context-meta'><div><span>Baseline</span><strong>{esc(view.project.baseline)}</strong></div><div><span>Candidate</span><strong>{esc(view.project.candidate)}</strong></div><div><span>Runtime</span><strong>{esc(view.project.runtime)}</strong></div></div></div></section>
<section class='decision'><div><div class='eyebrow'>Release Decision · Gate</div><div class='decision-status {status_class}'>{esc(view.decision.get('decision'))}</div><p class='decision-rationale'>{esc(view.decision.get('rationale'))}</p></div><ul class='decision-checks'>{''.join(f"<li>{esc(item.get('name', item.get('check', 'Gate check')))}：{esc(_decision_check_label(item.get('status', item.get('result', ''))))}</li>" for item in view.decision.get('checks', []))}</ul></section>
<section class='metrics'>{metrics}</section>
<div class='report-layout'><div class='narratives'>{''.join(sections_html)}</div>{aside}</div>
<footer>Evidence 状态：{esc(_status_label_zh(view.metrics.get('evidence_status')))} · Report hash 仅保留在技术元数据：<code>{esc(view.technical_metadata.get('report_hash'))}</code></footer></main></body></html>"""
    html = html.replace("</style>", f"{_HTML_STYLE_OVERRIDES}</style>", 1)
    status_fragment = f"<div class='decision-status {status_class}'>{esc(view.decision.get('decision'))}</div>"
    html = html.replace(status_fragment, f"<div class='decision-status {status_class}'>{esc(_decision_label(view.decision.get('decision')))}</div>", 1)
    html = html.replace("Release Decision · Gate", "评估结论 · EVALUATION RESULT", 1)
    if view.decision.get("presentation_note"):
        marker = f"<p class='decision-rationale'>{esc(view.decision.get('rationale'))}</p>"
        note = f"<div class='decision-evidence-note'>{esc(view.decision.get('presentation_note'))}</div>"
        html = html.replace(marker, marker + note, 1)
    return html


def _section(section: Any, body: str, *, class_name: str = "") -> str:
    classes = f"report-section {class_name}".strip()
    return f"<section class='{classes}'><div class='section-head'><span class='eyebrow'>{_esc(section.eyebrow)}</span><h2>{_esc(section.title)}</h2></div>{body}</section>"


def _overview_html(data: Mapping[str, Any], template: ProductReportTemplate, esc) -> str:
    fields = ((template.label("product_role"), data.get("product_role")), (template.label("why_it_exists"), data.get("why_it_exists")), (template.label("user_problem"), data.get("user_problem")), (template.label("boundary"), data.get("boundary")))
    cards = "".join(f"<div class='overview-item'><span class='card-label'>{esc(label)}</span><p>{esc(value)}</p></div>" for label, value in fields)
    ideal = "".join(f"<li>{esc(item)}</li>" for item in data.get("ideal_behavior", []))
    return f"<div class='overview-grid'>{cards}</div><h3>{esc(template.label('ideal_behavior'))}</h3><ul class='ideal'>{ideal}</ul>"


def _context_html(data: Mapping[str, Any], esc) -> str:
    rows = "".join(f"<tr><th>{esc(item.get('label'))}</th><td>{esc(item.get('value'))}</td></tr>" for item in data.get("items", []))
    return f"<table class='context-table'><tbody>{rows}</tbody></table>"


def _summary_html(data: Mapping[str, Any], decision: Mapping[str, Any], *, labels: Mapping[str, str], esc) -> str:
    findings = "".join(f"<article class='finding-card finding-{esc(item.get('finding_type', 'other'))}'><strong>{esc(item.get('title'))}</strong><p>{esc(item.get('statement'))}</p></article>" for item in data.get("main_findings", []))
    follow_up = "；".join(str(item) for item in data.get("follow_up_priorities", []))
    note = decision.get("presentation_note")
    note_html = f"<p class='decision-evidence-note'>{esc(note)}</p>" if note else ""
    return f"<div class='summary-callout'><strong>{esc(labels['final_conclusion'])}</strong><p>{esc(data.get('final_conclusion'))}</p></div><div class='finding-grid'>{findings}</div><p class='summary-recommendation'><strong>{esc(labels['product_recommendation'])}</strong>：{esc(data.get('product_recommendation'))}</p><p class='muted summary-followup'><strong>{esc(labels['follow_up_priorities'])}</strong>：{esc(follow_up)}</p>{note_html}"


def _dimensions_html(items: list[Mapping[str, Any]], template: ProductReportTemplate, esc) -> str:
    return "<div class='dimension-grid'>" + "".join(f"<article class='dimension'><span class='card-label'>{esc(_dimension_label(template, item.get('dimension', '')))}</span><strong>{esc(item.get('conclusion'))}</strong><p>{esc(item.get('explanation'))}</p></article>" for item in items) + "</div>"


def _experiments_overview_html(data: Mapping[str, Any], esc) -> str:
    cards = "".join(f"<article class='map-item'><span class='map-number'>{index + 1}</span><div><span class='card-label'>{esc(item.get('name'))}</span><h3>{esc(item.get('question'))}</h3><p>{esc(item.get('purpose'))}</p></div></article>" for index, item in enumerate(data.get("questions", [])))
    return f"<p class='lede'>{esc(data.get('summary'))}</p><div class='experiment-map'>{cards}</div>"


def _analysis_html(items: list[Mapping[str, Any]], template: ProductReportTemplate, esc) -> str:
    cards = []
    labels = template.labels
    label_keys = {
        "purpose": "experiment_purpose",
        "design": "experiment_design",
        "input_scenario": "input_scenario",
        "observation": "observation",
        "result": "result",
    }
    for item in items:
        grid = "".join(
            f"<div{_wide_class(key)}><span class='card-label'>{esc(labels[label_keys[key]])}</span><p>{esc(item.get(key))}</p></div>"
            for key in ("purpose", "design", "input_scenario", "observation", "result")
        )
        cards.append(f"<article class='analysis-card'><span class='card-label'>{esc(template.section('experiment_analysis').title)}</span><h3>{esc(item.get('display_name') or item.get('experiment_name'))}</h3><div class='analysis-grid'>{grid}</div><p class='summary-callout'><strong>{esc(labels['product_meaning'])}</strong>：{esc(item.get('product_meaning'))}</p></article>")
    return "<div class='analysis-list'>" + "".join(cards) + "</div>"


def _stability_html(data: Mapping[str, Any], template: ProductReportTemplate, esc) -> str:
    cards = "".join(f"<article class='scenario-card'><div class='scenario-heading'><div class='scenario-title'><h3>{esc(item.get('name'))}</h3><code>{esc(item.get('scenario_id'))}</code></div><span class='scenario-status'>{esc(item.get('status'))}</span></div><p class='prompt'>“{esc(item.get('user_prompt'))}”</p><div class='scenario-grid'><div><span class='card-label'>{esc(template.labels['scenario_purpose'])}</span><p>{esc(item.get('purpose'))}</p></div><div><span class='card-label'>{esc(template.labels['scenario_observation'])}</span><p>{esc(item.get('observation'))}</p></div><div class='wide'><span class='card-label'>{esc(template.labels['scenario_result'])}</span><p>{esc(item.get('result'))}</p></div></div></article>" for item in data.get("scenarios", []))
    return f"<p>{esc(data.get('summary'))}</p><p class='summary-callout'><strong>{esc(template.labels['scenario_conclusion'])}</strong>：{esc(data.get('coverage_conclusion'))}</p><div class='scenario-list'>{cards}</div>"


def _impact_html(data: Mapping[str, Any], esc) -> str:
    findings = "".join(f"<li><strong>{esc(item.get('impact_dimension'))}</strong>：{esc(item.get('product_meaning'))}<span class='priority'>{esc(item.get('severity'))}</span></li>" for item in data.get("findings", []))
    return f"<p class='summary-callout'>{esc(data.get('user_consequence'))}</p><p><strong>影响的用户旅程</strong>：{esc(data.get('affected_user_journey'))}</p><ul class='impact-list'>{findings}</ul>"


def _recommendations_html(items: list[Mapping[str, Any]], labels: Mapping[str, str], esc) -> str:
    return "<div class='recommendation-list'>" + "".join(f"<article class='recommendation'><span class='priority'>{esc(item.get('priority'))}</span><h3>{esc(item.get('target'))}</h3><p>{esc(item.get('action'))}</p><p class='muted'><strong>{esc(labels['reasoning'])}</strong>：{esc(item.get('reasoning'))}</p><p class='next-step'><strong>{esc(labels['next_step'])}</strong>：{esc('；'.join(item.get('validation_plan', [])))}</p></article>" for item in items) + "</div>"


def _limitations_html(items: list[Mapping[str, Any]], esc) -> str:
    return "<ul class='impact-list'>" + "".join(f"<li>{esc(item.get('statement'))}</li>" for item in items) + "</ul>"


def _evidence_html(view: NormalizedReport, esc) -> str:
    metrics = view.metrics
    stats = "".join(f"<div class='evidence-stat'><span class='card-label'>{esc(label)}</span><strong>{esc(value)}</strong></div>" for label, value in (("状态", _status_label_zh(view.evidence_bundle.get('status'))), ("已验证", metrics.get('verified_count')), ("通过", metrics.get('passed_count')), ("失败", metrics.get('failed_count')), ("成本", _cost_text(metrics.get('cost_usd')))))
    conditions = []
    for item in view.evidence_bundle.get("conditions", []):
        observations = item.get("observations", {})
        detail = "".join(f"<div><span>{esc(_observation_label(key))}</span><p>{esc(_stringify(value))}</p></div>" for key, value in observations.items())
        conditions.append(f"<details><summary>{esc(_condition_display_label(item))}</summary><div class='condition-meta'><span>{esc(item.get('kind_label', item.get('kind')))}</span><span class='condition-status {_status_class(item.get('status'))}'>{esc(_status_label_zh(item.get('status')))}</span><span>{esc(item.get('experiment_id'))}</span><span>{esc(item.get('scenario_id'))}</span></div><div class='evidence-detail'>{detail}<div><span>证据引用</span><p>{esc(', '.join(item.get('evidence_refs', [])))}</p></div></div></details>")
    return f"<p class='lede'>首屏展示证据状态、条件计数与总体摘要；具体启用、移除、替换条件保持折叠，展开后查看观察值与证据引用。</p><div class='evidence-summary'>{stats}</div>{''.join(conditions)}"


def _technical_html(evidence: Mapping[str, Any], metadata: Mapping[str, Any], esc) -> str:
    definitions = "".join(f"<dt>{esc(key)}</dt><dd>{esc(_stringify(value))}</dd>" for key, value in metadata.items())
    return f"<dl class='definition-list'>{definitions}</dl><p class='muted'>原始技术记录、事实与补充证据位于右侧 Technical Evidence 索引中，可展开查看。</p>"


def _condition_display_label(item: Mapping[str, Any]) -> str:
    labels = {
        "enabled": "启用 Skill 测试",
        "disabled": "移除 Skill 测试",
        "replacement": "替换实现测试",
    }
    return labels.get(str(item.get("kind") or ""), str(item.get("label") or "实验条件"))


def _decision_check_label(value: object) -> str:
    text = str(value or "").lower()
    if text in {"passed", "pass", "approve", "approved", "supported", "success", "complete", "completed"}:
        return "PASS"
    if text in {"failed", "fail", "block", "blocked", "error"}:
        return "BLOCKED"
    if text in {"review", "pending", "unresolved", "mixed"}:
        return "REVIEW"
    return str(value or "PENDING")


def _technical_side_html(evidence: Mapping[str, Any], esc) -> str:
    details = []
    for label, values in (("records", evidence.get("records", [])), ("facts", evidence.get("facts", [])), ("supplementary", evidence.get("supplementary", []))):
        for index, value in enumerate(values, start=1):
            details.append(f"<details><summary>{esc(label)} · {index}</summary><pre>{esc(json.dumps(value, ensure_ascii=False, indent=2))}</pre></details>")
    return "<div class='side-details'>" + "".join(details) + "</div>"


def _observation_label(key: str) -> str:
    labels = {
        "runtime_completed": "运行完成",
        "trace_event_count": "事件数量",
        "trace_types": "事件类型",
        "verifier": "校验器",
        "verifier_type": "校验器类型",
        "structured_output": "结构化输出",
        "constraint_adherence": "约束遵循",
        "side_effect_boundary": "副作用边界",
        "fallback_used": "是否使用回退",
        "deliverable_present": "交付物存在",
        "oracle_verified": "Oracle 已验证",
    }
    return labels.get(key, key.replace("_", " "))


def _interaction_html(interaction: Any, esc) -> str:
    rows = "".join(f"<tr><td>{esc(item.scenario_name)}</td><td>{esc(item.a_only)}</td><td>{esc(item.b_only)}</td><td>{esc(item.combined)}</td><td>{esc(item.product_meaning)}</td></tr>" for item in interaction.scenario_comparisons)
    semantics = ""
    if interaction.outcome_gain_status is not None:
        semantics += f"<p><strong>Observed outcome · {esc(interaction.outcome_gain_status)}</strong>：{esc(interaction.observed_outcome)}</p>"
    if interaction.mechanism_status is not None:
        semantics += f"<p><strong>Observed mechanism · {esc(interaction.mechanism_status)}</strong>：{esc(interaction.observed_mechanism)}</p>"
    return f"<article class='analysis-card'><span class='card-label'>Observed Metrics and Interaction Mechanisms · Skill Pair Scenario Comparison</span><h3>Skill Pair 对照</h3><p>{esc(interaction.summary)}</p>{semantics}<table class='context-table'><thead><tr><th>Scenario</th><th>A Only</th><th>B Only</th><th>A+B</th><th>Product Meaning</th></tr></thead><tbody>{rows}</tbody></table><p><strong>Synergy Gain interpretation</strong>：{esc(interaction.synergy_gain)}</p><p><strong>Reliability &amp; Cost Impact</strong>：{esc(interaction.reliability_cost)}</p></article>"


def _root_cause_html(findings: list[Any], esc) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{esc(item.observed_failure_type)}</td><td>{esc(item.root_cause_category)}</td>"
        f"<td>{esc(item.frequency)} / {esc(item.affected_trial_count)} trials / {esc(item.affected_scenario_count)} scenarios</td>"
        f"<td>{esc(item.stability)}</td><td>{esc(item.root_cause_confidence)}</td>"
        f"<td>{esc(item.analyst_hypothesis)}</td>"
        "</tr>"
        for item in findings
    )
    return f"<article class='analysis-card'><span class='card-label'>Recurring Failure Patterns</span><h3>Analyst Root-Cause Interpretation</h3><table class='context-table'><thead><tr><th>Observed failure</th><th>Hypothesis category</th><th>Support</th><th>Stability</th><th>Confidence</th><th>Interpretation</th></tr></thead><tbody>{rows}</tbody></table></article>"


def _markdown_analysis(item: Mapping[str, Any], labels: Mapping[str, str]) -> list[str]:
    lines = [f"### {item.get('display_name') or item.get('experiment_name', '')}", ""]
    label_keys = {
        "purpose": "experiment_purpose",
        "design": "experiment_design",
        "input_scenario": "input_scenario",
        "observation": "observation",
        "result": "result",
        "product_meaning": "product_meaning",
    }
    for key in ("purpose", "design", "input_scenario", "observation", "result", "product_meaning"):
        label = labels.get(label_keys[key], key)
        lines.extend([f"**{label}**：{item.get(key, '')}", ""])
    return lines


def _markdown_interaction(interaction: Any) -> list[str]:
    lines = ["### Observed Interaction Metrics and Mechanisms", "", "#### Skill Pair Scenario Comparison", "", interaction.summary, ""]
    if interaction.outcome_gain_status is not None:
        lines.extend([f"**Observed outcome ({interaction.outcome_gain_status})**：{interaction.observed_outcome}", ""])
    if interaction.mechanism_status is not None:
        lines.extend([f"**Observed mechanism ({interaction.mechanism_status})**：{interaction.observed_mechanism}", ""])
    lines.extend(["| Scenario | A Only | B Only | A+B | Product Meaning |", "| --- | --- | --- | --- | --- |"])
    lines.extend(f"| {item.scenario_name} | {item.a_only} | {item.b_only} | {item.combined} | {item.product_meaning} |" for item in interaction.scenario_comparisons)
    lines.extend(["", f"**Capability Contribution**：{interaction.capability_contribution}", "", f"**Composition Gain**：{interaction.composition_gain}", "", f"**Synergy Gain**：{interaction.synergy_gain}", "", f"**Coordination**：{interaction.coordination}", "", f"**Conflict / Interference**：{interaction.conflict}", "", f"**Reliability & Cost Impact**：{interaction.reliability_cost}", ""])
    return lines


def _markdown_fields(data: Mapping[str, Any], labels: Mapping[str, str], keys: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    for key in keys:
        lines.extend([f"**{labels[key]}**：{data.get(key, '')}", ""])
    return lines


def _dimension_label(template: ProductReportTemplate, dimension: str) -> str:
    return template.dimension_labels.get(dimension, dimension.replace("_", " ").title())


def _scenario_id_markdown(value: str | None) -> str:
    return f" (`scenario_id: {value}`)" if value else ""


def _suite_markdown(suite: Mapping[str, object] | None) -> list[str]:
    if not suite:
        return []
    coverage = suite.get("coverage") if isinstance(suite.get("coverage"), Mapping) else {}
    lines = [
        "### Evaluation Coverage",
        "",
        f"- Coverage status: {coverage.get('status', 'unavailable')}",
        f"- Scenarios executed / planned / intended: {coverage.get('executed_scenario_count', '-')} / "
        f"{coverage.get('planned_scenario_count', '-')} / {coverage.get('intended_scenario_count', '-')}",
        f"- Trials: {coverage.get('executed_trial_count', '-')} / {coverage.get('planned_trial_count', '-')}",
        f"- Repeated scenarios executed / intended: {coverage.get('repeated_scenario_count', '-')} / "
        f"{coverage.get('intended_repeated_scenario_count', '-')}",
        "",
        "| Category | Condition | Scenarios | Trials | Passed | Failed | Unresolved | Resolved coverage | Observed pass rate |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in suite.get("category_aggregates", []):
        if not isinstance(item, Mapping):
            continue
        rate = item.get("observed_success_rate")
        rate_text = "unavailable" if rate is None else f"{float(rate):.1%}"
        resolved_text = f"{item.get('resolved_count', 0)} / {item.get('trial_count', 0)}"
        lines.append(
            f"| {item.get('category', '')} | {item.get('condition_kind', '')} | "
            f"{item.get('scenario_count', 0)} | {item.get('trial_count', 0)} | "
            f"{item.get('verified_success_count', 0)} | {item.get('failure_count', 0)} | "
            f"{item.get('unresolved_count', 0)} | {resolved_text} | {rate_text} |"
        )
    for title, values in (("Derived metrics", suite.get("derived_metrics")), ("Routing", suite.get("routing"))):
        if not isinstance(values, Mapping):
            continue
        lines.extend(["", f"#### {title}", ""])
        lines.extend(
            f"- {key}: {value}"
            for key, value in values.items()
            if value is None or isinstance(value, (str, int, float, bool))
        )
    oracle_scope = suite.get("oracle_scope")
    if isinstance(oracle_scope, Mapping):
        lines.extend(["", "#### Oracle Scope", "", f"- Declared scopes: {', '.join(oracle_scope.get('declared_scopes', []))}", f"- Scoped trials: {oracle_scope.get('scoped_trial_count')} / {oracle_scope.get('total_trial_count')}"])
        lines.extend(f"- Limitation: {item}" for item in oracle_scope.get("limitations", []))
    failure_patterns = suite.get("failure_patterns")
    if isinstance(failure_patterns, list) and failure_patterns:
        lines.extend(["", "#### Recurring Failure Patterns", "", "| Failure | Status | Condition | Trials | Scenarios | Stability | Evidence refs |", "| --- | --- | --- | ---: | ---: | --- | --- |"])
        for item in failure_patterns:
            if isinstance(item, Mapping):
                lines.append(f"| {item.get('failure_type')} | {item.get('assertion_status', 'unavailable')} | {item.get('condition_kind')} | {item.get('affected_trial_count', item.get('frequency'))} | {item.get('affected_scenario_count', len(item.get('affected_scenario_ids', [])))} | {item.get('stability', 'unavailable')} | {', '.join(item.get('evidence_refs', []))} |")
    incidence = suite.get("failure_incidence")
    if isinstance(incidence, list) and incidence:
        lines.extend(["", "#### Typed Oracle Failure Incidence", "", "| Type | Failed | Resolved / Applicable | Unresolved | Observed rate | Affected conditions |", "| --- | ---: | ---: | ---: | ---: | --- |"])
        for item in incidence:
            if isinstance(item, Mapping):
                rate = item.get("observed_rate")
                lines.append(f"| {item.get('failure_type')} | {item.get('failure_count')} | {item.get('resolved_trial_count')} / {item.get('applicable_trial_count')} | {item.get('unresolved_count')} | {'unavailable' if rate is None else f'{float(rate):.1%}'} | {', '.join(item.get('affected_conditions', []))} |")
    scenario_routing = suite.get("scenario_routing")
    if isinstance(scenario_routing, list) and scenario_routing:
        lines.extend(["", "#### Scenario Routing", "", "| Scenario | N | A | B | Both | Neither | Empirical A / B share | Stability |", "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |"])
        for item in scenario_routing:
            if isinstance(item, Mapping):
                shares = "observed routing only" if item.get("a_empirical_share") is None else f"{float(item['a_empirical_share']):.1%} / {float(item['b_empirical_share']):.1%}"
                stability = "unavailable" if item.get("routing_stability") is None else f"{float(item['routing_stability']):.1%}"
                lines.append(f"| {item.get('scenario_id')} | {item.get('repetition_count')} | {item.get('a_selected_count')} | {item.get('b_selected_count')} | {item.get('both_selected_count')} | {item.get('neither_selected_count')} | {shares} | {stability} |")
    return [*lines, ""]


def _suite_html(suite: Mapping[str, object] | None, esc) -> str:
    if not suite:
        return ""
    coverage = suite.get("coverage") if isinstance(suite.get("coverage"), Mapping) else {}
    rows = []
    for item in suite.get("category_aggregates", []):
        if not isinstance(item, Mapping):
            continue
        rate = item.get("observed_success_rate")
        rate_text = "unavailable" if rate is None else f"{float(rate):.1%}"
        resolved_text = f"{item.get('resolved_count', 0)} / {item.get('trial_count', 0)}"
        rows.append(
            "<tr>"
            f"<td>{esc(item.get('category'))}</td><td>{esc(item.get('condition_kind'))}</td>"
            f"<td>{esc(item.get('scenario_count'))}</td><td>{esc(item.get('trial_count'))}</td>"
            f"<td>{esc(item.get('verified_success_count'))}</td><td>{esc(item.get('failure_count'))}</td>"
            f"<td>{esc(item.get('unresolved_count'))}</td><td>{esc(resolved_text)}</td><td>{esc(rate_text)}</td>"
            "</tr>"
        )
    oracle_scope = suite.get("oracle_scope")
    scope_html = ""
    if isinstance(oracle_scope, Mapping):
        scopes = ", ".join(str(item) for item in oracle_scope.get("declared_scopes", [])) or "unavailable"
        limits = "".join(f"<li>{esc(item)}</li>" for item in oracle_scope.get("limitations", []))
        scope_html = (
            f"<h3>Oracle Scope</h3><p><strong>Declared:</strong> {esc(scopes)} · "
            f"<strong>Scoped trials:</strong> {esc(oracle_scope.get('scoped_trial_count'))} / {esc(oracle_scope.get('total_trial_count'))}</p>"
            + (f"<ul>{limits}</ul>" if limits else "")
        )
    incidence_rows = "".join(
        "<tr>"
        f"<td>{esc(item.get('failure_type'))}</td><td>{esc(item.get('failure_count'))}</td>"
        f"<td>{esc(item.get('resolved_trial_count'))} / {esc(item.get('applicable_trial_count'))}</td>"
        f"<td>{esc(item.get('unresolved_count'))}</td>"
        f"<td>{esc(_observed_rate_text(item.get('observed_rate')))}</td>"
        "</tr>"
        for item in suite.get("failure_incidence", []) if isinstance(item, Mapping)
    )
    incidence_html = ("<h3>Typed Oracle Failure Incidence</h3><table class='context-table'><thead><tr><th>Type</th><th>Failed</th><th>Resolved / Applicable</th><th>Unresolved</th><th>Observed rate</th></tr></thead><tbody>" + incidence_rows + "</tbody></table>") if incidence_rows else ""
    pattern_rows = "".join(
        "<tr>"
        f"<td>{esc(item.get('failure_type'))}</td><td>{esc(item.get('assertion_status', 'unavailable'))}</td>"
        f"<td>{esc(item.get('condition_kind'))}</td><td>{esc(item.get('affected_trial_count', item.get('frequency')))}</td>"
        f"<td>{esc(item.get('affected_scenario_count', len(item.get('affected_scenario_ids', []))))}</td>"
        f"<td>{esc(item.get('stability', 'unavailable'))}</td>"
        "</tr>"
        for item in suite.get("failure_patterns", []) if isinstance(item, Mapping)
    )
    patterns_html = ("<h3>Recurring Failure Patterns</h3><table class='context-table'><thead><tr><th>Failure</th><th>Status</th><th>Condition</th><th>Trials</th><th>Scenarios</th><th>Stability</th></tr></thead><tbody>" + pattern_rows + "</tbody></table>") if pattern_rows else ""
    routing_rows = "".join(
        "<tr>"
        f"<td>{esc(item.get('scenario_id'))}</td><td>{esc(item.get('repetition_count'))}</td>"
        f"<td>{esc(item.get('a_selected_count'))}</td><td>{esc(item.get('b_selected_count'))}</td>"
        f"<td>{esc(item.get('both_selected_count'))}</td><td>{esc(item.get('neither_selected_count'))}</td>"
        f"<td>{esc(_routing_share_text(item))}</td>"
        "</tr>"
        for item in suite.get("scenario_routing", []) if isinstance(item, Mapping)
    )
    scenario_routing_html = ("<h3>Scenario Routing</h3><table class='context-table'><thead><tr><th>Scenario</th><th>N</th><th>A</th><th>B</th><th>Both</th><th>Neither</th><th>Empirical A / B share</th></tr></thead><tbody>" + routing_rows + "</tbody></table>") if routing_rows else ""
    return (
        "<article class='analysis-card'><span class='card-label'>Evaluation Coverage</span>"
        f"<h3>{esc(coverage.get('executed_scenario_count', '-'))} / "
        f"{esc(coverage.get('intended_scenario_count', '-'))} scenarios · "
        f"{esc(coverage.get('executed_trial_count', '-'))} trials · {esc(coverage.get('status', 'unavailable'))}</h3>"
        "<table class='context-table'><thead><tr><th>Category</th><th>Condition</th><th>Scenarios</th>"
        "<th>Trials</th><th>Passed</th><th>Failed</th><th>Unresolved</th><th>Resolved coverage</th><th>Observed pass rate</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        f"{_scalar_table_html('Derived metrics', suite.get('derived_metrics'), esc)}"
        f"{_scalar_table_html('Routing', suite.get('routing'), esc)}{scope_html}{patterns_html}{incidence_html}{scenario_routing_html}</article>"
    )


def _scalar_table_html(title: str, values: object, esc) -> str:
    if not isinstance(values, Mapping):
        return ""
    rows = "".join(
        f"<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>"
        for key, value in values.items()
        if value is None or isinstance(value, (str, int, float, bool))
    )
    return f"<h3>{esc(title)}</h3><table class='context-table'><tbody>{rows}</tbody></table>" if rows else ""


def _observed_rate_text(value: object) -> str:
    return "unavailable" if value is None else f"{float(value):.1%}"


def _routing_share_text(item: Mapping[str, object]) -> str:
    if item.get("a_empirical_share") is None:
        return "observed routing only"
    return f"{float(item['a_empirical_share']):.1%} / {float(item['b_empirical_share']):.1%}"


def _status_class(value: object) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("block", "fail", "failed", "reject")):
        return "failed"
    if any(token in text for token in ("review", "pending", "partial", "mixed")):
        return "review"
    return "supported"


def _status_label_zh(value: object) -> str:
    text = str(value or "").lower()
    if text in {"passed", "pass", "approve", "approved", "supported", "success", "complete", "completed"}:
        return "通过"
    if text in {"failed", "fail", "block", "blocked", "error"}:
        return "未通过"
    if text in {"review", "pending", "unresolved", "mixed"}:
        return "需复核"
    return str(value or "待处理")


def _decision_label(value: object) -> str:
    text = str(value or "").lower()
    return {"pass": "PASS", "approve": "PASS", "review": "REVIEW", "block": "BLOCKED"}.get(text, "PENDING")


def _wide_class(key: str) -> str:
    return " class='wide'" if key == "input_scenario" else ""


def _cost_text(value: object) -> str:
    return "未记录" if value is None else f"${float(value):.4f}"


def _stringify(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value) if value is not None else "-"


def _esc(value: object) -> str:
    return html.escape(_stringify(value), quote=True)


def _logo_svg() -> str:
    path = asset_path("frontend", "public", "icons", "agent-guard-logo.svg")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "<span aria-label='Agent Iteration Guard'>AIG</span>"


def write_product_evaluation_outputs(
    output_dir: Path,
    report: ProductEvaluationReport,
    template: ProductReportTemplate | None = None,
    *,
    project_context: Mapping[str, object] | None = None,
    gate: Mapping[str, object] | None = None,
) -> dict[str, Path]:
    """Persist every delivery format from one validated report object."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "evidence": output_dir / "product-evaluation-evidence.json",
        "report": output_dir / "product-evaluation-report.json",
        "html": output_dir / "product-evaluation-report.html",
        "markdown": output_dir / "product-evaluation-report.md",
    }
    paths["evidence"].write_text(report.evidence.model_dump_json(indent=2) + "\n", encoding="utf-8")
    paths["report"].write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    paths["html"].write_text(render_product_evaluation_html(report, template, project_context=project_context, gate=gate), encoding="utf-8")
    paths["markdown"].write_text(render_product_evaluation_markdown(report, template, project_context=project_context, gate=gate), encoding="utf-8")
    return paths


__all__ = ["render_product_evaluation_html", "render_product_evaluation_markdown", "write_product_evaluation_outputs"]
