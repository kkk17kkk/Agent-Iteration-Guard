import React from "react";
import { I, Status, Page, SectionHeading, EmptyState, Metric, FileButton, ProjectContextCard } from "../components.jsx";
import { API_BASE, pathFor } from "../lib.js";

function ReportHistory({ reportList, onOpen, onRefresh, loading }) {
  return (
    <section className="section-block report-history">
      <SectionHeading label="History" title="报告历史" />
      <div className="button-row" style={{ marginTop: 0, marginBottom: 14 }}>
        <button className="secondary" onClick={onRefresh} disabled={loading}><I name="refresh" />刷新列表</button>
      </div>
      <div className="capability-list">
        {reportList.map((item) => (
          <button className="capability-row" key={item.report_id} onClick={() => onOpen(item.report_id, item.run_id)}>
            <span className="cap-icon cap-skill"><I name="doc" /></span>
            <div><strong className="mono">{item.report_id}</strong><span className="muted">Run {item.run_id || "-"}{item.created_at ? ` · ${item.created_at}` : ""}</span></div>
            <span className="row-tail"><I name="arrowRight" /></span>
          </button>
        ))}
        {!reportList.length && <p className="muted">暂无已持久化的报告。</p>}
      </div>
    </section>
  );
}

function TextList({ items = [], className = "bullet-list" }) {
  return <ul className={className}>{items.map((item, index) => <li key={item?.id || index}>{typeof item === "string" ? item : item?.statement || item?.product_meaning || "-"}</li>)}</ul>;
}

function DefinitionList({ items }) {
  return <dl className="definition-list">{Object.entries(items || {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd className={key.includes("hash") || key.includes("id") ? "mono" : ""}>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "-")}</dd></div>)}</dl>;
}

function EvidenceCondition({ condition }) {
  const observations = condition.observations || {};
  return (
    <details className="evidence-condition evidence-condition-collapsed">
      <summary><span className="condition-summary-title">{condition.label || condition.condition_id}</span><span className="condition-summary-kind">{condition.kind}</span><Status value={condition.status} /></summary>
      <div className="evidence-condition-meta"><span>实验 {condition.experiment_id}</span><span>场景 {condition.scenario_id}</span><span>Refs {condition.evidence_refs?.length || 0}</span></div>
      <div className="evidence-observation-grid">{Object.entries(observations).map(([key, value]) => <div key={key}><span>{key}</span><strong>{typeof value === "object" ? JSON.stringify(value) : String(value)}</strong></div>)}</div>
      <p className="muted">证据引用：{condition.evidence_refs?.join("、") || "-"}</p>
      {condition.record && <details className="evidence-details"><summary>查看原始记录</summary><pre>{JSON.stringify(condition.record, null, 2)}</pre></details>}
    </details>
  );
}

function ReportContent({ view, report, reportList, onOpen, onRefresh, loading }) {
  const sections = view;
  const summary = sections.summary || {};
  const metrics = sections.metrics || {};
  const experiments = sections.experiments || {};
  const evidence = sections.evidence_bundle || {};
  const technical = sections.technical_metadata || {};
  const decision = sections.decision || {};
  const labels = {
    final: "最终结论",
    recommendation: "产品建议",
    followUp: "后续优化重点",
    purpose: "Purpose",
    design: "Design",
    input: "Input Scenario",
    observation: "Observation",
    result: "Result",
    meaning: "Product Meaning",
  };

  return (
    <>
      <section className={`decision-band d-${decision.decision || "pending"}`}>
        <div className="decision-info">
          <span className="eyebrow">Release Decision · 发布决策</span>
          <strong className={`decision ${decision.decision || "pending"}`}>{decision.decision || "pending"}</strong>
          <div className="decision-copy">{decision.rationale || "报告已加载，但尚未经过确定性 Gate 评估。"}</div>
        </div>
        <div className="decision-check-list">{(decision.checks || []).map((check) => <div className="check-row" key={check.name}><Status value={check.status} /><strong>{check.name}</strong><span>{check.detail}</span></div>)}</div>
      </section>

      <section className="summary-grid report-summary-grid">
        <Metric label="报告状态" value={metrics.report_status || "-"} />
        <Metric label="Evidence" value={metrics.evidence_status || "-"} />
        <Metric label="实验总数" value={metrics.experiment_count ?? "-"} />
        <Metric label="Findings" value={metrics.findings_count ?? "-"} />
      </section>

      <div className="report-single-stack">
        <section className="section-block report-section-card">
          <SectionHeading label="1 · Capability Overview" title="能力概览" />
          <div className="report-overview-grid">{[["能力职责", sections.capability_overview?.product_role], ["为什么需要", sections.capability_overview?.why_it_exists], ["用户问题", sections.capability_overview?.user_problem], ["能力边界", sections.capability_overview?.boundary]].map(([label, value]) => <div className="report-overview-item" key={label}><span>{label}</span><p>{value || "-"}</p></div>)}</div>
          <h3>理想行为</h3><TextList items={sections.capability_overview?.ideal_behavior} />
        </section>

        <section className="section-block report-section-card"><SectionHeading label="2 · Evaluation Context" title="评估上下文" /><div className="context-table-wrap"><table className="context-table"><tbody>{(sections.evaluation_context?.items || []).map((item) => <tr key={item.label}><th>{item.label}</th><td>{item.value}</td></tr>)}</tbody></table></div></section>

        <section className="section-block report-section-card"><SectionHeading label="3 · Executive Summary" title="评估结果汇总" /><div className="report-callout"><strong>{labels.final}</strong><p>{summary.final_conclusion || "-"}</p></div><div className="finding-list">{(summary.main_findings || []).map((item) => <div className="finding-card" key={item.title}><strong>{item.title}</strong><p>{item.statement}</p></div>)}</div><p className="large-copy"><strong>{labels.recommendation}：</strong>{summary.product_recommendation || "-"}</p><p className="muted"><strong>{labels.followUp}：</strong>{(summary.follow_up_priorities || []).join("；") || "-"}</p></section>

        <section className="section-block report-section-card"><SectionHeading label="4 · Evaluation Dimensions" title="评估维度" /><div className="report-dimension-grid">{(sections.dimensions || []).map((item) => <div className="report-dimension-card" key={item.dimension}><span>{item.dimension}</span><strong>{item.conclusion}</strong><p>{item.explanation}</p></div>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="5 · Experiment Overview" title="实验地图" /><p className="large-copy">{experiments.summary || "-"}</p><div className="experiment-map-list">{(experiments.questions || []).map((item, index) => <div className="experiment-map-item" key={item.name}><span className="map-number">{index + 1}</span><div><strong>{item.name}</strong><h3>{item.question}</h3><p>{item.purpose}</p></div></div>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="6 · Experiment Analysis" title="实验明细" /><div className="report-analysis-list">{(experiments.analysis || []).map((item) => <article className="report-analysis-card" key={item.experiment_name}><h3>{item.experiment_name}</h3><div className="report-analysis-grid">{[[labels.purpose, item.purpose], [labels.design, item.design], [labels.input, item.input_scenario], [labels.observation, item.observation], [labels.result, item.result]].map(([label, value]) => <div key={label}><span>{label}</span><p>{value || "-"}</p></div>)}</div><p className="report-callout"><strong>{labels.meaning}：</strong>{item.product_meaning}</p></article>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="7 · Scenario Stability" title="场景稳定性" /><p className="large-copy">{sections.scenario_stability?.summary || "-"}</p><div className="report-callout"><strong>覆盖结论：</strong>{sections.scenario_stability?.coverage_conclusion || "-"}</div><div className="scenario-list">{(sections.scenario_stability?.scenarios || []).map((item) => <article className="scenario-card" key={item.scenario_id}><div className="scenario-card-head"><strong>{item.name}</strong><Status value={item.status} /></div><p className="scenario-prompt">“{item.user_prompt}”</p><p><strong>目标：</strong>{item.purpose}</p><p><strong>观察：</strong>{item.observation}</p><p><strong>结果：</strong>{item.result}</p></article>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="8 · Impact / Capability Impact" title="能力影响" /><div className="report-callout">{sections.impact?.user_consequence || "-"}</div><p><strong>影响的用户旅程：</strong>{sections.impact?.affected_user_journey || "-"}</p><TextList items={sections.impact?.findings} /></section>

        <section className="section-block report-section-card"><SectionHeading label="9 · Recommendation" title="建议行动" /><div className="recommendation-list">{(sections.recommendations || []).map((item, index) => <article className="reco-item" key={item.recommendation_id || index}><span className="reco-index">{index + 1}</span><div><strong>{item.target}</strong><p>{item.action}</p><p className="muted">依据：{item.reasoning}</p><p className="next-step">下一步：{(item.validation_plan || []).join("；")}</p></div></article>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="10 · Limitations" title="评估边界及限制" /><TextList items={sections.limitations} /></section>

        <section className="section-block report-section-card"><SectionHeading label="11 · Evidence" title="实验证据 / 技术证据" /><p className="large-copy">首屏只展示证据状态、计数、成本与条件数量；具体条件保持折叠。</p><div className="summary-grid evidence-summary-grid"><Metric label="Evidence 状态" value={evidence.status || "-"} /><Metric label="已验证" value={metrics.verified_count ?? "-"} /><Metric label="通过" value={metrics.passed_count ?? "-"} /><Metric label="失败" value={metrics.failed_count ?? "-"} /><Metric label="成本" value={metrics.cost_usd == null ? "未记录" : `$${Number(metrics.cost_usd).toFixed(4)}`} /></div><div className="evidence-condition-list">{(evidence.conditions || []).map((condition) => <EvidenceCondition key={condition.condition_id} condition={condition} />)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="12 · Technical Metadata" title="技术元数据" /><DefinitionList items={technical} /><details className="technical-details"><summary>查看技术记录、事实与补充证据</summary><pre>{JSON.stringify(sections.technical_evidence || {}, null, 2)}</pre></details></section>
      </div>

      <ReportHistory reportList={reportList} onOpen={onOpen} onRefresh={onRefresh} loading={loading} />
      <div className="report-export-hooks" hidden>
        <span data-report-id={report.report_id} />
      </div>
    </>
  );
}

export default function Report({ projectId, projectHeader, report, reportView, reportList, onOpen, onImport, onRefresh, loading, onOverview, demoMode }) {
  const exportReport = (format) => {
    if (!report?.report_id) return;
    const endpoint = demoMode
      ? `${API_BASE}/api/v1/demo/reports/lighttable/export?format=${format}`
      : `${API_BASE}${pathFor(projectId, `/reports/${encodeURIComponent(report.report_id)}/export?format=${format}`)}`;
    window.open(endpoint, "_blank");
  };

  if (!projectHeader) return <EmptyState icon="doc" title="请先加载项目" detail="评估报告需要先加载一个 Project Intelligence。" />;

  if (!report) {
    return <Page title="评估报告" kicker="报告中心 · REPORT" intro="打开服务端报告或导入经过校验的 Report JSON。" before={<ProjectContextCard {...projectHeader} onOverview={onOverview} />}><section className="section-block"><SectionHeading label="Open Report" title="打开或导入报告" /><div className="button-row" style={{ marginTop: 0 }}><FileButton id="report-import-empty" accept="application/json,.json" onChange={onImport}>导入 Report JSON</FileButton><button className="secondary" onClick={onRefresh} disabled={loading}><I name="refresh" />刷新报告列表</button></div>{reportList.length ? <ReportHistory reportList={reportList} onOpen={onOpen} onRefresh={onRefresh} loading={loading} /> : <EmptyState icon="doc" title="尚未选择报告" detail="完成一次 Run 并生成报告，或导入已有报告 JSON。" />}</section></Page>;
  }

  if (!reportView) return <Page title={report.subject?.component_name || report.report_id} kicker="报告视图加载中" intro="正在从统一报告视图加载章节与证据。" before={<ProjectContextCard {...projectHeader} onOverview={onOverview} />}><section className="section-block"><p className="large-copy">报告视图正在加载，请稍候。</p></section></Page>;

  return <Page title={reportView.title} kicker="单 Skill 评估 · SINGLE SKILL EVALUATION" intro={reportView.capability_overview?.product_role || "基于真实评估证据的能力报告。"} before={<ProjectContextCard {...projectHeader} onOverview={onOverview} />}>
    <div className="report-toolbar"><FileButton id="report-import-existing" accept="application/json,.json" onChange={onImport}>替换报告</FileButton><button className="secondary" onClick={() => exportReport("json")}><I name="download" />导出 JSON</button><button className="secondary" onClick={() => exportReport("md")}><I name="download" />导出 Markdown</button><button className="secondary" onClick={() => exportReport("html")}><I name="download" />导出 HTML</button></div>
    <ReportContent view={reportView} report={report} reportList={reportList} onOpen={onOpen} onRefresh={onRefresh} loading={loading} />
  </Page>;
}
