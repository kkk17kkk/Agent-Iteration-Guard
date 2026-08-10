import React, { useState } from "react";
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
  return <dl className="definition-list">{Object.entries(items || {}).filter(([key]) => key !== "raw_report_keys").map(([key, value]) => <div key={key}><dt>{key}</dt><dd className={key.includes("hash") || key.includes("id") ? "mono" : ""}>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "-")}</dd></div>)}</dl>;
}

const REPORT_DIMENSION_GUIDE = [
  ["Trigger · 能力触发", "确认任务是否真正进入目标 Skill 的能力流程，而不是只产生表面相似的输出。"],
  ["Execution · 流程执行", "确认 Skill 是否完成了声明的约束识别、判断和执行步骤。"],
  ["Delivery · 结果交付", "确认最终交付物是否结构化、可执行，并满足用户任务要求。"],
  ["Boundary · 能力边界", "确认边界场景、异常输入和副作用是否符合声明契约。"],
];

const OBSERVATION_LABELS = {
  runtime_completed: "运行完成",
  target_completed: "目标完成",
  trace_event_count: "事件数量",
  trace_types: "事件类型",
  verifier: "校验器",
  verifier_type: "校验器类型",
  structured_output: "结构化输出",
  output_recorded: "输出已记录",
  constraint_adherence: "约束遵循",
  side_effect_boundary: "副作用边界",
  fallback_used: "是否使用回退",
  deliverable_present: "交付物存在",
  oracle_verified: "独立校验已完成",
  oracle_outcome: "校验结果",
};

function statusWord(value) {
  const key = String(value || "").toLowerCase();
  if (["pass", "passed", "approve", "approved", "supported", "success"].includes(key)) return "PASS";
  if (["block", "blocked", "failed", "fail"].includes(key)) return "BLOCKED";
  if (["review", "pending", "unresolved", "mixed"].includes(key)) return "REVIEW";
  return "PENDING";
}

function evidenceGateWord(value) {
  const key = String(value || "").toLowerCase();
  if (key === "approve") return "已通过";
  if (key === "review") return "需复核";
  if (key === "block") return "未通过，需处理证据完整性";
  return "尚未评估";
}

function conditionTitle(condition) {
  return {
    enabled: "启用 Skill 测试",
    disabled: "移除 Skill 测试",
    replacement: "替换实现测试",
  }[condition.kind] || condition.label || condition.condition_id;
}

function EvidenceCondition({ condition }) {
  const observations = condition.observations || {};
  return (
    <details className="evidence-condition evidence-condition-collapsed">
      <summary><span className="condition-summary-title">{conditionTitle(condition)}</span><span className="condition-summary-kind">{condition.kind_label || condition.kind}</span><Status value={statusWord(condition.status)} /></summary>
      <div className="evidence-condition-meta"><span>实验 {condition.experiment_id}</span><span>场景 {condition.scenario_id}</span><span>Refs {condition.evidence_refs?.length || 0}</span></div>
      <div className="evidence-observation-grid">{Object.entries(observations).map(([key, value]) => <div key={key}><span>{OBSERVATION_LABELS[key] || key}</span><strong>{typeof value === "object" ? JSON.stringify(value) : String(value)}</strong></div>)}</div>
      <p className="muted">证据引用：{condition.evidence_refs?.join("、") || "-"}</p>
      {condition.record && <details className="evidence-details"><summary>查看原始记录</summary><pre>{JSON.stringify(condition.record, null, 2)}</pre></details>}
    </details>
  );
}

function AggregateMetrics({ title, values }) {
  const entries = Object.entries(values || {}).filter(([, value]) => value == null || ["string", "number", "boolean"].includes(typeof value));
  if (!entries.length) return null;
  return <><h3>{title}</h3><div className="table-wrap"><table><tbody>{entries.map(([name, value]) => <tr key={name}><th>{name}</th><td>{value == null ? "unavailable" : String(value)}</td></tr>)}</tbody></table></div></>;
}

function ReportContent({ view, report, reportList, onOpen, onRefresh, loading }) {
  const [dimensionInfoOpen, setDimensionInfoOpen] = useState(false);
  const sections = view;
  const summary = sections.summary || {};
  const metrics = sections.metrics || {};
  const experiments = sections.experiments || {};
  const evaluationSuite = sections.evaluation_suite || null;
  const interaction = sections.interaction_analysis || null;
  const rootCauses = sections.root_cause_findings || [];
  const failurePatterns = evaluationSuite?.failure_patterns || [];
  const oracleScope = evaluationSuite?.oracle_scope || null;
  const failureIncidence = evaluationSuite?.failure_incidence || [];
  const scenarioRouting = evaluationSuite?.scenario_routing || [];
  const evidence = sections.evidence_bundle || {};
  const technical = sections.technical_metadata || {};
  const decision = sections.decision || {};
  const labels = {
    final: "最终结论",
    recommendation: "建议",
    followUp: "后续优化重点",
    purpose: "目的 Purpose",
    design: "设计 Design",
    input: "输入场景 Input Scenario",
    observation: "观察 Observation",
    result: "结果 Result",
    meaning: "建议",
  };
  const evidenceGate = decision.evidence_gate || {};

  return (
    <>
      <section className={`decision-band d-${decision.decision || "pending"}`}>
        <div className="decision-info">
          <span className="eyebrow">评估结论 · EVALUATION RESULT</span>
          <strong className={`decision ${decision.decision || "pending"}`}>{statusWord(decision.decision)}</strong>
          <div className="decision-copy">{decision.rationale || "报告已加载，但尚未经过确定性 Gate 评估。"}</div>
          {evidenceGate.decision && evidenceGate.decision !== "approve" && <div className="decision-evidence-note">技术证据门禁：{evidenceGateWord(evidenceGate.decision)}。这不会改写当前评估结论。</div>}
        </div>
        <div className="decision-check-list">{(decision.checks || []).map((check) => <div className="check-row" key={check.name}><Status value={statusWord(check.status)} /><strong>{check.name}</strong><span>{check.detail}</span></div>)}</div>
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

        <section className="section-block report-section-card report-summary-section"><SectionHeading label="3 · Executive Summary" title="评估结果汇总" /><div className="report-callout"><strong>{labels.final}</strong><p>{summary.final_conclusion || "-"}</p></div><div className="finding-grid">{(summary.main_findings || []).map((item) => <div className={`finding-card finding-${item.finding_type || "other"}`} key={item.title}><strong>{item.title}</strong><p>{item.statement}</p></div>)}</div><p className="large-copy report-recommendation"><strong>{labels.recommendation}：</strong>{summary.product_recommendation || "-"}</p><p className="muted report-followup"><strong>{labels.followUp}：</strong>{(summary.follow_up_priorities || []).join("；") || "-"}</p></section>

        <section className="section-block report-section-card"><div className="section-heading"><div><span className="eyebrow">4 · Evaluation Dimensions</span><h2>评估维度 <button type="button" className="info-button report-info-button" aria-label="查看评估维度说明" onClick={() => setDimensionInfoOpen(true)}><I name="info" size={15} /></button></h2></div></div><div className="report-dimension-grid">{(sections.dimensions || []).map((item) => <div className="report-dimension-card" key={item.dimension}><span>{item.label || item.dimension}</span><strong>{item.conclusion}</strong><p>{item.explanation}</p></div>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="5 · Experiment Overview" title="实验地图" /><p className="large-copy">{experiments.summary || "-"}</p><div className="experiment-map-list">{(experiments.questions || []).map((item, index) => <div className="experiment-map-item" key={item.name}><span className="map-number">{index + 1}</span><div><strong>{item.name}</strong><h3>{item.question}</h3><p>{item.purpose}</p></div></div>)}</div>{evaluationSuite && <><h3>Evaluation Coverage</h3><div className="summary-grid evidence-summary-grid"><Metric label="覆盖状态" value={evaluationSuite.coverage?.status || "-"} /><Metric label="场景（执行 / 目标）" value={`${evaluationSuite.coverage?.executed_scenario_count ?? "-"} / ${evaluationSuite.coverage?.intended_scenario_count ?? "-"}`} /><Metric label="Trials（执行 / 计划）" value={`${evaluationSuite.coverage?.executed_trial_count ?? "-"} / ${evaluationSuite.coverage?.planned_trial_count ?? "-"}`} /><Metric label="重复采样（执行 / 目标）" value={`${evaluationSuite.coverage?.repeated_scenario_count ?? "-"} / ${evaluationSuite.coverage?.intended_repeated_scenario_count ?? "-"}`} /></div><div className="table-wrap"><table><thead><tr><th>Category</th><th>Condition</th><th>N</th><th>Trials</th><th>Passed</th><th>Failed</th><th>Unresolved</th><th>Resolved coverage</th><th>Observed pass rate</th></tr></thead><tbody>{(evaluationSuite.category_aggregates || []).map((item) => <tr key={`${item.category}:${item.condition_kind}`}><td>{item.category}</td><td>{item.condition_kind}</td><td>{item.scenario_count}</td><td>{item.trial_count}</td><td>{item.verified_success_count}</td><td>{item.failure_count}</td><td>{item.unresolved_count}</td><td>{`${item.resolved_count ?? 0} / ${item.trial_count ?? 0}`}</td><td>{item.observed_success_rate == null ? "unavailable" : `${(Number(item.observed_success_rate) * 100).toFixed(1)}%`}</td></tr>)}</tbody></table></div><AggregateMetrics title="Derived metrics" values={evaluationSuite.derived_metrics} /><AggregateMetrics title="Routing" values={evaluationSuite.routing} /></>}</section>

        <section className="section-block report-section-card"><SectionHeading label="6 · Experiment Analysis" title="实验明细" /><div className="report-analysis-list">{(experiments.analysis || []).map((item) => <article className="report-analysis-card" key={item.experiment_name}><h3>{item.display_name || item.experiment_name}</h3><div className="report-analysis-grid">{[[labels.purpose, item.purpose], [labels.design, item.design], [labels.input, item.input_scenario], [labels.observation, item.observation], [labels.result, item.result]].map(([label, value]) => <div key={label}><span>{label}</span><p>{value || "-"}</p></div>)}</div><p className="report-callout"><strong>{labels.meaning}：</strong>{item.product_meaning}</p></article>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="7 · Scenario Stability" title="场景稳定性" /><p className="large-copy">{sections.scenario_stability?.summary || "-"}</p><div className="report-callout"><strong>覆盖结论：</strong>{sections.scenario_stability?.coverage_conclusion || "-"}</div><div className="scenario-list">{(sections.scenario_stability?.scenarios || []).map((item) => <article className="scenario-card" key={item.scenario_id}><div className="scenario-card-head"><strong>{item.name}</strong><Status value={statusWord(item.status)} /></div><p className="scenario-prompt">“{item.user_prompt}”</p><p><strong>目标：</strong>{item.purpose}</p><p><strong>观察：</strong>{item.observation}</p><p><strong>结果：</strong>{item.result}</p></article>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="8 · Impact / Capability Impact" title="能力影响" /><div className="report-callout">{sections.impact?.user_consequence || "-"}</div><p><strong>影响的用户旅程：</strong>{sections.impact?.affected_user_journey || "-"}</p><TextList items={sections.impact?.findings} /></section>

        <section className="section-block report-section-card"><SectionHeading label="9 · Recommendation" title="建议行动" /><div className="recommendation-list">{(sections.recommendations || []).map((item, index) => <article className="reco-item" key={item.recommendation_id || index}><span className="reco-index">{index + 1}</span><div><strong>{item.target}</strong><p>{item.action}</p><p className="muted">依据：{item.reasoning}</p><p className="next-step">下一步：{(item.validation_plan || []).join("；")}</p></div></article>)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="10 · Limitations" title="评估边界及限制" /><TextList items={sections.limitations} /></section>

        <section className="section-block report-section-card"><SectionHeading label="11 · Evidence" title="实验证据 / 技术证据" /><p className="large-copy">首屏只展示证据状态、计数、成本与条件数量；具体条件保持折叠。当前技术证据门禁：{evidenceGateWord(evidenceGate.decision)}。</p><div className="summary-grid evidence-summary-grid"><Metric label="Evidence 状态" value={evidence.status || "-"} /><Metric label="已验证" value={metrics.verified_count ?? "-"} /><Metric label="通过" value={metrics.passed_count ?? "-"} /><Metric label="失败" value={metrics.failed_count ?? "-"} /><Metric label="成本" value={metrics.cost_usd == null ? "未记录" : `$${Number(metrics.cost_usd).toFixed(4)}`} /></div><div className="evidence-condition-list">{(evidence.conditions || []).map((condition) => <EvidenceCondition key={condition.condition_id} condition={condition} />)}</div></section>

        <section className="section-block report-section-card"><SectionHeading label="12 · Technical Metadata" title="技术元数据" /><DefinitionList items={technical} /><details className="technical-details"><summary>查看技术记录、事实与补充证据</summary><pre>{JSON.stringify(sections.technical_evidence || {}, null, 2)}</pre></details></section>
      </div>

      {dimensionInfoOpen && <div className="modal-backdrop" role="presentation" onMouseDown={() => setDimensionInfoOpen(false)}><section className="modal-card quality-info-modal" role="dialog" aria-modal="true" aria-labelledby="report-dimension-info-title" onMouseDown={(event) => event.stopPropagation()}><div className="modal-card-head"><div><span className="eyebrow">EVALUATION DIMENSIONS</span><h2 id="report-dimension-info-title">评估维度说明</h2></div><button type="button" className="icon-button" aria-label="关闭评估维度说明" onClick={() => setDimensionInfoOpen(false)}><I name="x" /></button></div><div className="quality-dimension-list">{REPORT_DIMENSION_GUIDE.map(([name, detail]) => <div className="quality-dimension-item" key={name}><strong>{name}</strong><p>{detail}</p></div>)}</div></section></div>}

      {interaction && <section className="section-block report-section-card"><SectionHeading label="Interaction Semantics" title="Observed Outcome vs Interaction Mechanism" /><div className="summary-grid evidence-summary-grid"><Metric label="Outcome status" value={interaction.outcome_gain_status || "unavailable"} /><Metric label="Mechanism status" value={interaction.mechanism_status || "unavailable"} /></div><div className="report-callout"><strong>Observed outcome</strong><p>{interaction.observed_outcome || "Not present in this artifact version"}</p></div><div className="report-callout"><strong>Observed mechanism</strong><p>{interaction.observed_mechanism || "Not present in this artifact version"}</p></div></section>}

      {(failurePatterns.length > 0 || rootCauses.length > 0) && <section className="section-block report-section-card"><SectionHeading label="Recurring Failure Patterns" title="Evidence-grounded RCA" />{failurePatterns.length > 0 && <div className="table-wrap"><table><thead><tr><th>Verified pattern</th><th>Status</th><th>Condition</th><th>Trials</th><th>Scenarios</th><th>Stability</th></tr></thead><tbody>{failurePatterns.map((item) => <tr key={`${item.failure_type}:${item.condition_kind}:${item.assertion_status}`}><td>{item.failure_type}</td><td>{item.assertion_status || "unavailable"}</td><td>{item.condition_kind}</td><td>{item.affected_trial_count ?? item.frequency}</td><td>{item.affected_scenario_count ?? item.affected_scenario_ids?.length ?? "-"}</td><td>{item.stability || "unavailable"}</td></tr>)}</tbody></table></div>}{rootCauses.length > 0 && <><h3>Analyst Interpretation</h3><div className="table-wrap"><table><thead><tr><th>Observed failure</th><th>Category</th><th>Support</th><th>Stability</th><th>Confidence</th></tr></thead><tbody>{rootCauses.map((item) => <tr key={item.finding_id}><td>{item.observed_failure_type}</td><td>{item.root_cause_category}</td><td>{`${item.frequency} / ${item.affected_trial_count} trials / ${item.affected_scenario_count} scenarios`}</td><td>{item.stability}</td><td>{item.root_cause_confidence}</td></tr>)}</tbody></table></div>{rootCauses.map((item) => <div className="report-callout" key={`${item.finding_id}:hypothesis`}><strong>{item.observed_failure_type}</strong><p>{item.analyst_hypothesis}</p></div>)}</>}</section>}

      {(oracleScope || failureIncidence.length > 0) && <section className="section-block report-section-card"><SectionHeading label="Oracle Semantics" title="Verification Scope and Typed Failure Incidence" />{oracleScope && <><div className="summary-grid evidence-summary-grid"><Metric label="Declared scope" value={(oracleScope.declared_scopes || []).join(", ") || "unavailable"} /><Metric label="Scoped trials" value={`${oracleScope.scoped_trial_count ?? 0} / ${oracleScope.total_trial_count ?? 0}`} /></div><TextList items={oracleScope.limitations || []} /></>} {failureIncidence.length > 0 && <div className="table-wrap"><table><thead><tr><th>Failure type</th><th>Failed</th><th>Resolved / Applicable</th><th>Unresolved</th><th>Observed rate</th></tr></thead><tbody>{failureIncidence.map((item) => <tr key={item.failure_type}><td>{item.failure_type}</td><td>{item.failure_count}</td><td>{`${item.resolved_trial_count} / ${item.applicable_trial_count}`}</td><td>{item.unresolved_count}</td><td>{item.observed_rate == null ? "unavailable" : `${(Number(item.observed_rate) * 100).toFixed(1)}%`}</td></tr>)}</tbody></table></div>}</section>}

      {scenarioRouting.length > 0 && <section className="section-block report-section-card"><SectionHeading label="Scenario Routing" title="Repeated Routing-sensitive Scenarios" /><div className="table-wrap"><table><thead><tr><th>Scenario</th><th>N</th><th>A</th><th>B</th><th>Both</th><th>Neither</th><th>Empirical A / B share</th><th>Stability</th></tr></thead><tbody>{scenarioRouting.map((item) => <tr key={item.scenario_id}><td>{item.scenario_id}</td><td>{item.repetition_count}</td><td>{item.a_selected_count}</td><td>{item.b_selected_count}</td><td>{item.both_selected_count}</td><td>{item.neither_selected_count}</td><td>{item.a_empirical_share == null ? "observed routing only" : `${(Number(item.a_empirical_share) * 100).toFixed(1)}% / ${(Number(item.b_empirical_share) * 100).toFixed(1)}%`}</td><td>{item.routing_stability == null ? "unavailable" : `${(Number(item.routing_stability) * 100).toFixed(1)}%`}</td></tr>)}</tbody></table></div></section>}

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
