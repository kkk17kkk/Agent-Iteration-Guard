"""Presentation projections for ProductEvaluationReport."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .product_evaluation_report import ProductEvaluationReport
from .product_report_template import ProductReportTemplate, default_product_report_template


def render_product_evaluation_markdown(
    report: ProductEvaluationReport,
    template: ProductReportTemplate | None = None,
) -> str:
    """Render the portable report as a product-facing Markdown document."""

    template = template or default_product_report_template()
    sections = {section.section_id: section for section in template.sections}
    labels = template.labels
    title = template.title_format.replace("{component_name}", report.subject.component_name)
    lines = [
        f"# {title}",
        "",
        f"## {sections['capability_overview'].eyebrow}",
        f"### {sections['capability_overview'].title}",
        "",
        f"**{labels['product_role']}**：{report.product_overview.product_role}",
        "",
        f"**{labels['why_it_exists']}**：{report.product_overview.why_it_exists}",
        "",
        f"**{labels['user_problem']}**：{report.product_overview.user_problem}",
        "",
        f"**{labels['ideal_behavior']}**：",
        *[f"- {item}" for item in report.product_overview.ideal_behavior],
        "",
        f"**{labels['boundary']}**：{report.product_overview.boundary}",
        "",
        f"## {sections['evaluation_context'].eyebrow}",
        f"### {sections['evaluation_context'].title}",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        *[f"| {item.label} | {item.value} |" for item in report.evaluation_context.items],
        "",
        f"## {sections['executive_summary'].eyebrow}",
        f"### {sections['executive_summary'].title}",
        "",
        f"**{labels['final_conclusion']}**：{report.executive_summary.final_conclusion}",
        "",
        f"**{labels['main_findings']}**：",
        *[f"- **{item.title}**：{item.statement}" for item in report.executive_summary.main_findings],
        "",
        f"**{labels['product_recommendation']}**：{report.executive_summary.product_recommendation}",
        "",
        f"**{labels['follow_up_priorities']}**：",
        *[f"- {item}" for item in report.executive_summary.follow_up_priorities],
        "",
    ]
    for dimension in _ordered_dimensions(report):
        lines.extend([
            f"### {_dimension_label(template, dimension.dimension)}",
            "",
            f"{dimension.conclusion}。{dimension.explanation}",
            "",
        ])
    lines.extend([
        f"## {sections['experiment_overview'].eyebrow}",
        f"### {sections['experiment_overview'].title}",
        "",
        report.experiment_overview.summary,
        "",
    ])
    for item in report.experiment_overview.questions:
        lines.extend([
            f"### {item.name}",
            "",
            f"**{labels['experiment_question']}**：{item.question}",
            "",
            f"**{labels['experiment_purpose_short']}**：{item.purpose}",
            "",
        ])
    lines.extend([
        f"## {sections['experiment_analysis'].eyebrow}",
        f"### {sections['experiment_analysis'].title}",
        "",
    ])
    for item in report.experiment_analysis:
        lines.extend([
            f"### {item.experiment_name}",
            "",
            f"**{labels['experiment_purpose']}**：{item.purpose}",
            "",
            f"**{labels['experiment_design']}**：{item.design}",
            "",
            f"**{labels['input_scenario']}**：{item.input_scenario}",
            "",
            f"**{labels['observation']}**：{item.observation}",
            "",
            f"**{labels['result']}**：{item.result}",
            "",
            f"**{labels['product_meaning']}**：{item.product_meaning}",
            "",
        ])
    if report.interaction_analysis is not None:
        interaction = report.interaction_analysis
        lines.extend([
            "### Skill Pair Scenario Comparison",
            "",
            interaction.summary,
            "",
            "| Scenario | A Only | B Only | A+B | Product Meaning |",
            "| --- | --- | --- | --- | --- |",
            *[
                f"| {item.scenario_name} ({item.category}) | {item.a_only} | {item.b_only} | {item.combined} | {item.product_meaning} |"
                for item in interaction.scenario_comparisons
            ],
            "",
            f"**Capability Contribution**：{interaction.capability_contribution}",
            "",
            f"**Composition Gain**：{interaction.composition_gain}",
            "",
            f"**Synergy Gain**：{interaction.synergy_gain}",
            "",
            f"**Coordination**：{interaction.coordination}",
            "",
            f"**Conflict / Interference**：{interaction.conflict}",
            "",
            f"**Reliability & Cost Impact**：{interaction.reliability_cost}",
            "",
        ])
    lines.extend([
        f"## {sections['scenario_stability'].eyebrow}",
        f"### {sections['scenario_stability'].title}",
        "",
        f"{report.scenario_stability.summary}",
        "",
            f"**{labels['scenario_conclusion']}**：{report.scenario_stability.coverage_conclusion}",
        "",
    ])
    for item in report.scenario_stability.scenarios:
        lines.extend([
            f"### {item.name}{_scenario_id_markdown(item.scenario_id)}",
            "",
            f"**{labels['scenario_user']}**：{item.user_prompt}",
            "",
            f"**目标**：{item.purpose}",
            "",
            f"**观察**：{item.observation}",
            "",
            f"**结果**：{item.result}",
            "",
        ])
    lines.extend([
        f"## {sections['product_impact'].eyebrow}",
        f"### {sections['product_impact'].title}",
        "",
        f"**{labels['affected_user_journey']}**：{report.business_impact.affected_user_journey}",
        "",
        report.business_impact.user_consequence,
        "",
        *[f"- {item.product_meaning}" for item in report.findings],
        "",
        f"## {sections['recommendation'].eyebrow}",
        f"### {sections['recommendation'].title}",
        "",
    ])
    for item in report.recommendations:
        lines.extend([
            f"### {item.target}（{item.priority}）",
            "",
            item.action,
            "",
            f"**{labels['reasoning']}**：{item.reasoning}",
            "",
            f"**{labels['next_step']}**：{'；'.join(item.validation_plan)}",
            "",
        ])
    lines.extend([
        f"## {sections['limitations'].eyebrow}",
        f"### {sections['limitations'].title}",
        "",
        *[f"- {item.statement}" for item in report.limitations],
        "",
        f"## {template.sidebar('experiment_evidence').eyebrow}",
        f"### {template.sidebar('experiment_evidence').title}",
        "",
    ])
    for item in report.evidence_explorer.experiment_evidence:
        lines.extend([
            f"### {item.experiment_name}",
            "",
        f"**{labels['evidence_input_task']}**：{item.input_task}",
            "",
            f"**{item.reference_label}**：{item.reference_result}",
            "",
            f"**{item.changed_label}**：{item.changed_result}",
            "",
            f"**{labels['evidence_difference']}**：{item.difference}",
            "",
        ])
    if report.supplementary_evidence:
        lines.extend([
            "## Supplementary Benchmark Evidence",
            "",
            "These imported results are external evidence only; AIG did not execute the benchmark and they do not replace local oracle evidence.",
            "",
            "| Benchmark | Metric | Before | After | Unit | Evidence |",
            "| --- | --- | ---: | ---: | --- | --- |",
            *[
                f"| {item.benchmark_name} | {metric.metric_name} | {metric.baseline_value:g} | {metric.candidate_value:g} | {metric.unit} | {item.evidence_id} |"
                for item in report.supplementary_evidence
                for metric in item.metrics
            ],
            "",
        ])
    return "\n".join(lines)


def render_product_evaluation_html(
    report: ProductEvaluationReport,
    template: ProductReportTemplate | None = None,
) -> str:
    """Render a product report in a dark, audit-oriented two-column layout."""

    template = template or default_product_report_template()
    sections = {section.section_id: section for section in template.sections}
    labels = template.labels
    title = template.title_format.replace("{component_name}", report.subject.component_name)
    esc = lambda value: html.escape(str(value))
    status = template.status_labels.get(report.executive_summary.status, report.executive_summary.status)
    gate_class = "supported" if report.executive_summary.status == "supported" else "review"
    context_rows = "".join(
        f"<tr><th>{esc(item.label)}</th><td>{esc(item.value)}</td></tr>"
        for item in report.evaluation_context.items
    )
    dimensions = "".join(
        f"<article class='dimension'><span class='dimension-label'>{esc(_dimension_label(template, item.dimension))}</span>"
        f"<strong>{esc(item.conclusion)}</strong><p>{esc(item.explanation)}</p></article>"
        for item in _ordered_dimensions(report)
    )
    findings = "".join(
        f"<article class='finding'><span class='finding-mark'>✓</span><div><strong>{esc(item.title)}</strong>"
        f"<p>{esc(item.statement)}</p></div></article>"
        for item in report.executive_summary.main_findings
    )
    experiment_questions = "".join(
        f"<article class='experiment-map-item'><span class='map-number'>{index + 1}</span><div>"
        f"<span class='evidence-kind'>{esc(item.name)}</span><h3>{esc(item.question)}</h3>"
        f"<p>{esc(item.purpose)}</p></div></article>"
        for index, item in enumerate(report.experiment_overview.questions)
    )
    analyses = "".join(
        f"<article class='analysis-card'><div class='card-heading'><span class='evidence-kind'>{esc(sections['experiment_analysis'].title)}</span>"
        f"<h3>{esc(item.experiment_name)}</h3></div><div class='analysis-grid'>"
        f"<div><span>{esc(labels['experiment_purpose'])}</span><p>{esc(item.purpose)}</p></div>"
        f"<div><span>{esc(labels['experiment_design'])}</span><p>{esc(item.design)}</p></div>"
        f"<div class='wide'><span>{esc(labels['input_scenario'])}</span><p>{esc(item.input_scenario)}</p></div>"
        f"<div><span>{esc(labels['observation'])}</span><p>{esc(item.observation)}</p></div>"
        f"<div><span>{esc(labels['result'])}</span><p>{esc(item.result)}</p></div></div>"
        f"<p class='interpretation'><strong>{esc(labels['product_meaning'])}</strong>{esc(item.product_meaning)}</p></article>"
        for item in report.experiment_analysis
    )
    interaction_comparison = ""
    if report.interaction_analysis is not None:
        interaction = report.interaction_analysis
        comparison_rows = "".join(
            f"<tr><th>{esc(item.scenario_name)}<br><span class='muted'>{esc(item.category)}</span></th>"
            f"<td>{esc(item.a_only)}</td><td>{esc(item.b_only)}</td><td>{esc(item.combined)}</td>"
            f"<td>{esc(item.product_meaning)}</td></tr>"
            for item in interaction.scenario_comparisons
        )
        interaction_comparison = (
            "<article class='analysis-card interaction-comparison'>"
            f"<div class='card-heading'><span class='evidence-kind'>Skill Pair Scenario Comparison</span>"
            f"<h3>{esc(report.subject.component_name)}</h3></div>"
            f"<p class='lede'>{esc(interaction.summary)}</p>"
            "<table class='context-table'><thead><tr><th>Scenario</th><th>A Only</th>"
            "<th>B Only</th><th>A+B</th><th>Product Meaning</th></tr></thead>"
            f"<tbody>{comparison_rows}</tbody></table>"
            "<div class='analysis-grid'>"
            f"<div><span>Capability Contribution</span><p>{esc(interaction.capability_contribution)}</p></div>"
            f"<div><span>Composition Gain</span><p>{esc(interaction.composition_gain)}</p></div>"
            f"<div><span>Synergy Gain</span><p>{esc(interaction.synergy_gain)}</p></div>"
            f"<div><span>Coordination</span><p>{esc(interaction.coordination)}</p></div>"
            f"<div><span>Conflict / Interference</span><p>{esc(interaction.conflict)}</p></div>"
            f"<div class='wide'><span>Reliability &amp; Cost Impact</span><p>{esc(interaction.reliability_cost)}</p></div>"
            "</div></article>"
        )
    scenarios = "".join(
        f"<article class='scenario-card'><div class='scenario-heading'><div class='scenario-title'><span>{esc(item.name)}</span>"
        f"{_scenario_id_markup(item.scenario_id, esc)}</div>"
        f"<strong>{esc(template.status_labels.get(item.status, item.status))}</strong></div>"
        f"<p class='prompt'>“{esc(item.user_prompt)}”</p><div class='scenario-grid'>"
        f"<div><span>{esc(labels['scenario_purpose'])}</span><p>{esc(item.purpose)}</p></div>"
        f"<div><span>{esc(labels['scenario_observation'])}</span><p>{esc(item.observation)}</p></div>"
        f"<div class='wide'><span>{esc(labels['scenario_result'])}</span><p>{esc(item.result)}</p></div></div></article>"
        for item in report.scenario_stability.scenarios
    )
    findings_impact = "".join(
        f"<li><strong>{esc(item.impact_dimension)}</strong>：{esc(item.product_meaning)}"
        f"<span>{esc(item.severity)}</span></li>"
        for item in report.findings
    )
    recommendations = "".join(
        f"<article class='recommendation'><div><span class='priority'>{esc(item.priority)}</span>"
        f"<h3>{esc(item.target)}</h3></div><p>{esc(item.action)}</p>"
        f"<p class='muted'>{esc(item.reasoning)}</p><p class='next-step'><strong>{esc(labels['next_step'])}</strong>"
        f"{esc('；'.join(item.validation_plan))}</p></article>"
        for item in report.recommendations
    )
    limitations = "".join(f"<li>{esc(item.statement)}</li>" for item in report.limitations)
    product_evidence = "".join(
        f"<article class='side-evidence'><span>{esc(item.label)}</span><p>{esc(item.statement)}</p></article>"
        for item in report.evidence_explorer.product_evidence
    )
    experiment_evidence = "".join(_render_evidence_entry(item, esc) for item in report.evidence_explorer.experiment_evidence)
    technical_records = "".join(
        f"<details class='technical-record'><summary>{esc(record.record_type)} · {esc(record.source_ref)}</summary>"
        f"<pre>{esc(json.dumps(record.model_dump(mode='json'), ensure_ascii=False, indent=2))}</pre></details>"
        for record in report.evidence.records
    )
    technical_facts = "".join(
        f"<details class='technical-record'><summary>{esc(fact.label)}</summary>"
        f"<pre>{esc(json.dumps(fact.model_dump(mode='json'), ensure_ascii=False, indent=2))}</pre></details>"
        for fact in report.evidence.facts
    )
    supplementary_evidence = "".join(
        f"<details class='technical-record'><summary>{esc(item.benchmark_name)} · external benchmark evidence</summary>"
        f"<p class='technical'>AIG imported this result and did not execute the external benchmark.</p>"
        f"<pre>{esc(json.dumps(item.model_dump(mode='json'), ensure_ascii=False, indent=2))}</pre></details>"
        for item in report.supplementary_evidence
    )
    scenario_notice = ""
    if len(report.scenario_stability.scenarios) < 3:
        scenario_notice = f"<div class='sample-banner warning'>{esc(template.limited_scenario_notice_format.format(count=len(report.scenario_stability.scenarios)))}</div>"
    product_panel = template.sidebar("product_evidence")
    experiment_panel = template.sidebar("experiment_evidence")
    technical_panel = template.sidebar("technical_evidence")
    footer = template.footer_format.format(
        evidence_status=report.evaluation.evidence_status,
        report_hash=report.report_hash,
    )
    return f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='dark'>
<title>{esc(title)}</title>
<style>
:root{{--bg:#070b14;--surface:#0d1423;--surface-2:#111a2d;--line:#24314a;--text:#f3f6fb;--muted:#91a0b8;--accent:#4f7cff;--good:#42d6a4;--warn:#ffb454;--bad:#ff5f70;--radius:14px}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 76% 0,#12213f 0,transparent 32%),var(--bg);color:var(--text);font:15px/1.6 'Segoe UI','Microsoft YaHei UI',system-ui,sans-serif}}
a{{color:inherit}}code{{font-family:'Cascadia Code','SFMono-Regular',monospace;font-size:12px;color:#9eb7ff;overflow-wrap:anywhere}}
.shell{{max-width:1440px;margin:auto;padding:30px}}.topbar{{display:flex;align-items:center;justify-content:space-between;padding:0 0 22px;border-bottom:1px solid var(--line)}}
.brand{{display:flex;gap:12px;align-items:center;font-weight:700}}.mark{{display:grid;place-items:center;width:38px;height:38px;border:1px solid #6688ff;border-radius:12px;color:#a9bbff;background:#101a33}}
.meta{{color:var(--muted);font-size:13px}}.breadcrumb{{margin:26px 0 8px;color:var(--muted)}}h1{{font-size:34px;line-height:1.2;margin:0 0 9px;letter-spacing:-.02em}}h2{{font-size:20px;margin:4px 0 0}}h3{{font-size:17px;margin:4px 0 0}}.lede{{max-width:850px;color:#c4cee0;margin:0}}
.sample-banner{{margin:18px 0;padding:10px 14px;border:1px solid #6c5827;background:#211b0e;color:#ffd78b;border-radius:var(--radius)}}.sample-banner.warning{{border-color:#754d2c;background:#281912;color:#ffc17e}}
.panel{{background:linear-gradient(145deg,rgba(18,28,48,.96),rgba(11,17,30,.96));border:1px solid var(--line);border-radius:var(--radius);padding:22px}}
.content{{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px;align-items:start}}.narratives{{display:grid;gap:18px}}.section-heading{{margin-bottom:16px}}.section-heading span,.eyebrow{{display:block;color:#8fa1be;font-size:12px;letter-spacing:.08em;text-transform:uppercase}}
.overview-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.overview-item{{padding:15px;background:#0a1120;border:1px solid #2a3957;border-radius:10px}}.overview-item span,.context-table th,.analysis-grid span,.scenario-grid span{{display:block;color:var(--muted);font-size:12px}}
.overview-item p,.analysis-grid p,.scenario-grid p{{margin:4px 0;color:#c8d2e4}}.ideal{{margin:12px 0 0;padding-left:22px;color:#dfe6f4}}
.context-table{{width:100%;border-collapse:collapse;margin-top:8px}}.context-table th,.context-table td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}.context-table th{{width:160px;color:#aebeff;font-weight:600}}.context-table td{{color:#dfe6f4}}
.executive{{position:relative;overflow:hidden}}.executive:after{{content:'';position:absolute;inset:auto -90px -110px auto;width:280px;height:280px;border-radius:50%;background:radial-gradient(circle,rgba(79,124,255,.18),transparent 68%)}}.gate{{font-size:26px;line-height:1.2;margin:12px 0 8px;font-weight:800}}.gate.supported{{color:var(--good)}}.gate.review{{color:var(--warn)}}.executive .recommendation-line{{margin-top:18px;padding-top:14px;border-top:1px solid var(--line);color:#dfe6f4}}
.finding{{display:flex;gap:12px;padding:13px 0;border-bottom:1px solid var(--line)}}.finding:last-child{{border-bottom:0}}.finding-mark{{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#123c35;color:var(--good);font-weight:800}}.finding p{{margin:3px 0;color:#c8d2e4}}
.dimension-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px}}.dimension{{padding:14px;border:1px solid #2a3957;border-radius:10px;background:#0a1120}}.dimension-label{{display:block;color:#aebeff;font-size:12px}}.dimension strong{{display:block;margin-top:7px}}.dimension p{{margin:5px 0 0;color:#c8d2e4}}
.experiment-map{{display:grid;gap:8px}}.experiment-map-item{{display:grid;grid-template-columns:30px 1fr;gap:12px;align-items:start;padding:14px;border:1px solid #2a3957;border-radius:10px;background:#0a1120}}.map-number{{display:grid;place-items:center;width:26px;height:26px;border-radius:50%;background:#17264a;color:#a9bbff;font-weight:700}}.experiment-map-item h3{{margin:3px 0}}.experiment-map-item p{{margin:3px 0;color:#c8d2e4}}
.analysis-card,.scenario-card,.recommendation{{border-top:1px solid var(--line);padding:16px 0}}.analysis-card:first-child,.scenario-card:first-child,.recommendation:first-child{{border-top:0;padding-top:0}}.card-heading{{margin-bottom:10px}}.evidence-kind{{font-size:12px;color:#aebeff}}.analysis-grid,.scenario-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.analysis-grid>div,.scenario-grid>div{{padding-top:9px;border-top:1px solid var(--line)}}.analysis-grid .wide,.scenario-grid .wide{{grid-column:1/-1}}.interpretation{{margin:14px 0 0;padding:14px 16px;border-left:3px solid var(--accent);background:#0a1020;color:#dfe6f4}}.interpretation strong{{display:block;color:#aebeff;font-size:12px;margin-bottom:4px}}
.scenario-heading{{display:flex;justify-content:space-between;gap:10px;align-items:center}}.scenario-title{{display:flex;align-items:center;gap:10px;min-width:0}}.scenario-title>span{{color:#aebeff;font-weight:700}}.scenario-id{{color:var(--muted);font-size:11px;font-weight:500}}.scenario-heading strong{{color:var(--good);font-size:12px}}.prompt{{padding:10px 12px;margin:12px 0;background:#0a1020;color:#dfe6f4;border-left:3px solid #6688ff}}
.finding-list,.limits ul{{list-style:none;margin:12px 0 0;padding:0}}.finding-list li{{position:relative;padding:11px 0;border-bottom:1px solid var(--line)}}.finding-list li:last-child{{border-bottom:0}}.finding-list li span{{float:right;color:#ffd080;font-size:12px}}
.priority{{float:right;color:#ffd080;font-size:12px}}.recommendation h3{{display:inline-block}}.recommendation p{{margin:7px 0;color:#dfe6f4}}.muted{{color:var(--muted)!important}}.next-step{{color:#aebeff!important}}.next-step strong{{margin-right:8px}}
.sidebar{{display:grid;gap:18px;position:sticky;top:18px}}.side-evidence{{padding:12px 0;border-bottom:1px solid var(--line)}}.side-evidence:last-child{{border-bottom:0}}.side-evidence span{{color:#aebeff;font-size:12px}}.side-evidence p{{margin:4px 0;color:#c8d2e4}}details{{border-top:1px solid var(--line);padding:12px 0}}details:first-of-type{{border-top:0}}summary{{cursor:pointer;font-weight:700;color:#dfe6f4}}.evidence-value{{margin:10px 0;padding:10px;background:#0a1120;border-radius:9px;color:#c8d2e4}}.evidence-value strong{{display:block;color:var(--muted);font-size:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;background:#101827;color:#dbe7ff;padding:12px;border-radius:10px}}.technical{{color:var(--muted);font-size:13px}}
footer{{margin-top:22px;padding:18px 0;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}}
@media(max-width:980px){{.content{{grid-template-columns:1fr}}.sidebar{{position:static}}}}@media(max-width:680px){{.shell{{padding:18px}}.topbar{{align-items:flex-start;gap:12px}}.overview-grid,.dimension-grid,.analysis-grid,.scenario-grid{{grid-template-columns:1fr}}.analysis-grid .wide,.scenario-grid .wide{{grid-column:auto}}}}
@media print{{body{{background:#fff;color:#111}}.panel{{background:#fff;color:#111;border-color:#bbb}}.sidebar{{position:static}}}}
</style></head><body><main class='shell'>
<header class='topbar'><div class='brand'><div class='mark'>AIG</div><div>{esc(template.brand_name)}<br><span class='meta'>{esc(template.report_label)}</span></div></div><div class='meta'>{esc(template.language_label)}</div></header>
<div class='breadcrumb'>{esc(template.breadcrumb_prefix)} / {esc(report.subject.component_name)}</div><h1>{esc(title)}</h1><p class='lede'>{esc(report.product_overview.product_role)}</p>{scenario_notice}
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['capability_overview'].eyebrow)}</span><h2>{esc(sections['capability_overview'].title)}</h2></div><div class='overview-grid'><div class='overview-item'><span>{esc(labels['product_role'])}</span><p>{esc(report.product_overview.product_role)}</p></div><div class='overview-item'><span>{esc(labels['why_it_exists'])}</span><p>{esc(report.product_overview.why_it_exists)}</p></div><div class='overview-item'><span>{esc(labels['user_problem'])}</span><p>{esc(report.product_overview.user_problem)}</p></div><div class='overview-item'><span>{esc(labels['boundary'])}</span><p>{esc(report.product_overview.boundary)}</p></div></div><h3>{esc(labels['ideal_behavior'])}</h3><ul class='ideal'>{''.join(f'<li>{esc(item)}</li>' for item in report.product_overview.ideal_behavior)}</ul></section>
<div class='content'><div class='narratives'>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['evaluation_context'].eyebrow)}</span><h2>{esc(sections['evaluation_context'].title)}</h2></div><table class='context-table'><tbody>{context_rows}</tbody></table></section>
<section class='panel executive'><div class='section-heading'><span>{esc(sections['executive_summary'].eyebrow)}</span><h2>{esc(sections['executive_summary'].title)}</h2></div><div class='gate {gate_class}'>{esc(status)}</div><p>{esc(report.executive_summary.final_conclusion)}</p><div class='finding-list'>{findings}</div><p class='recommendation-line'><strong>{esc(labels['product_recommendation'])}</strong>　{esc(report.executive_summary.product_recommendation)}</p><p class='muted'><strong>{esc(labels['follow_up_priorities'])}</strong>　{esc('；'.join(report.executive_summary.follow_up_priorities))}</p></section>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(template.dimensions_eyebrow)}</span><h2>{esc(template.dimensions_title)}</h2></div><div class='dimension-grid'>{dimensions}</div></section>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['experiment_overview'].eyebrow)}</span><h2>{esc(sections['experiment_overview'].title)}</h2></div><p class='lede'>{esc(report.experiment_overview.summary)}</p><div class='experiment-map'>{experiment_questions}</div></section>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['experiment_analysis'].eyebrow)}</span><h2>{esc(sections['experiment_analysis'].title)}</h2></div>{analyses}{interaction_comparison}</section>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['scenario_stability'].eyebrow)}</span><h2>{esc(sections['scenario_stability'].title)}</h2></div><p>{esc(report.scenario_stability.summary)}</p><p class='interpretation'><strong>{esc(labels['scenario_conclusion'])}</strong>{esc(report.scenario_stability.coverage_conclusion)}</p>{scenarios}</section>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['product_impact'].eyebrow)}</span><h2>{esc(sections['product_impact'].title)}</h2></div><p>{esc(report.business_impact.user_consequence)}</p><ul class='finding-list'>{findings_impact}</ul></section>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['recommendation'].eyebrow)}</span><h2>{esc(sections['recommendation'].title)}</h2></div>{recommendations}</section>
<section class='panel narrative-panel'><div class='section-heading'><span>{esc(sections['limitations'].eyebrow)}</span><h2>{esc(sections['limitations'].title)}</h2></div><ul class='limits ul'>{limitations}</ul></section>
 </div><aside class='sidebar'><section class='panel'><span class='eyebrow'>{esc(product_panel.eyebrow)}</span><h2>{esc(product_panel.title)}</h2><p class='technical'>{esc(product_panel.description)}</p>{product_evidence}</section><section class='panel'><span class='eyebrow'>{esc(experiment_panel.eyebrow)}</span><h2>{esc(experiment_panel.title)}</h2><p class='technical'>{esc(experiment_panel.description)}</p>{experiment_evidence}</section><section class='panel'><span class='eyebrow'>{esc(technical_panel.eyebrow)}</span><h2>{esc(technical_panel.title)}</h2><p class='technical'>{esc(technical_panel.description)}</p>{supplementary_evidence}{technical_records}{technical_facts}</section></aside></div>
<footer>{esc(footer)}</footer></main></body></html>"""


def _render_evidence_entry(item: object, esc) -> str:
    return (
        f"<details class='evidence-entry'><summary>{esc(item.experiment_name)}</summary>"
        f"<div class='evidence-value'><strong>输入任务</strong>{esc(item.input_task)}</div>"
        f"<div class='evidence-value'><strong>{esc(item.reference_label)}</strong>{esc(item.reference_result)}</div>"
        f"<div class='evidence-value'><strong>{esc(item.changed_label)}</strong>{esc(item.changed_result)}</div>"
        f"<div class='evidence-value'><strong>差异</strong>{esc(item.difference)}</div></details>"
    )


def _scenario_id_markup(scenario_id: str | None, esc) -> str:
    if not scenario_id:
        return ""
    return f"<code class='scenario-id'>scenario_id: {esc(scenario_id)}</code>"


def _scenario_id_markdown(scenario_id: str | None) -> str:
    return f" (`scenario_id: {scenario_id}`)" if scenario_id else ""


def _ordered_dimensions(report: ProductEvaluationReport):
    order = {
        "trigger": 0,
        "execution": 1,
        "delivery": 2,
        "boundary": 3,
        "capability_contribution": 4,
        "synergy_gain": 5,
        "coordination": 6,
        "conflict": 7,
        "reliability_cost": 8,
    }
    return sorted(report.executive_summary.dimensions, key=lambda item: order.get(item.dimension, 99))


def _dimension_label(template: ProductReportTemplate, dimension: str) -> str:
    return template.dimension_labels.get(dimension, dimension.replace("_", " ").title())


def write_product_evaluation_outputs(
    output_dir: Path,
    report: ProductEvaluationReport,
    template: ProductReportTemplate | None = None,
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
    paths["html"].write_text(render_product_evaluation_html(report, template), encoding="utf-8")
    paths["markdown"].write_text(render_product_evaluation_markdown(report, template), encoding="utf-8")
    return paths


__all__ = [
    "render_product_evaluation_html",
    "render_product_evaluation_markdown",
    "write_product_evaluation_outputs",
]
