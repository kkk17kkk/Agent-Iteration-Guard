import React from "react";
import { I, Status, Page, SectionHeading, EmptyState, Metric, DecisionShield, FileButton } from "../components.jsx";
import { API_BASE, pathFor } from "../lib.js";

function conditionStatus(condition) {
  const observations = condition?.observations || {};
  if (observations.oracle_verified === true && observations.target_completed !== false) return "passed";
  if (observations.oracle_verified === false || observations.target_completed === false) return "failed";
  return "review";
}

function conditionRecord(condition, records) {
  const observations = condition?.observations || {};
  return records.find((record) => {
    const candidate = record?.payload?.observations || record?.observations || {};
    return candidate.scenario_id === condition.scenario_id && candidate.condition_kind === observations.condition_kind;
  });
}

function EvidenceCondition({ condition, records }) {
  const observations = condition.observations || {};
  const record = conditionRecord(condition, records);
  const payload = record?.payload || record || {};
  const outcome = payload.oracle?.outcome || (conditionStatus(condition) === "passed" ? "verified" : "unresolved");
  return (
    <div className="evidence-condition">
      <div className="evidence-condition-head">
        <Status value={conditionStatus(condition)} />
        <strong>{condition.label || condition.condition_id}</strong>
        <span className="component-type">{observations.scenario_category || condition.scenario_id}</span>
        <span className="row-tail">{outcome}</span>
      </div>
      <div className="evidence-condition-meta">
        <span>Latency {Number.isFinite(Number(observations.latency_ms)) ? `${observations.latency_ms} ms` : "-"}</span>
        <span>Cost {Number.isFinite(Number(observations.cost_usd)) ? `$${Number(observations.cost_usd).toFixed(6)}` : "-"}</span>
        <span>Trace {observations.trace_event_count ?? "-"}</span>
        <span>Refs {condition.evidence_refs?.length || 0}</span>
      </div>
      {payload.oracle?.summary && <p className="muted evidence-oracle">{payload.oracle.summary}</p>}
      {record && (
        <details className="evidence-details">
          <summary>查看 Trace / Output / Oracle</summary>
          <pre>{JSON.stringify({ trace: payload.trace, output: payload.output, oracle: payload.oracle }, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function ReportHistory({ reportList, onOpen, onRefresh, loading }) {
  return (
    <section className="section-block">
      <SectionHeading label="History" title="服务端报告" />
      <div className="button-row" style={{ marginTop: 0, marginBottom: 14 }}>
        <button className="secondary" onClick={onRefresh} disabled={loading}><I name="refresh" />刷新列表</button>
      </div>
      <div className="capability-list">
        {reportList.map((item) => (
          <button className="capability-row" key={item.report_id} onClick={() => onOpen(item.report_id, item.run_id)}>
            <span className="cap-icon cap-skill"><I name="doc" /></span>
            <div>
              <strong className="mono">{item.report_id}</strong>
              <span className="muted">Run {item.run_id}{item.created_at ? ` · ${item.created_at}` : ""}</span>
            </div>
            <span className="row-tail"><I name="arrowRight" /></span>
          </button>
        ))}
        {!reportList.length && <p className="muted">服务端暂无已持久化的报告。</p>}
      </div>
    </section>
  );
}

export default function Report({ projectId, report, evidence, gate, reportList, onOpen, onImport, onRefresh, loading }) {
  const interaction = report?.interaction_analysis;
  const exportReport = (format) => {
    if (report?.report_id) window.open(`${API_BASE}${pathFor(projectId, `/reports/${encodeURIComponent(report.report_id)}/export?format=${format}`)}`, "_blank");
  };

  if (!report) {
    return (
      <Page title="评估报告" kicker="发布决策 · RELEASE DECISION" intro="打开一份服务端持久化的报告，或导入经过校验的 CLI ProductEvaluationReport JSON。">
        <div className="stack">
          <section className="section-block">
            <SectionHeading label="Open Report" title="打开或导入报告" />
            <div className="button-row" style={{ marginTop: 0 }}>
              <FileButton id="report-import-empty" accept="application/json,.json" onChange={onImport}>导入 Report JSON</FileButton>
              <button className="secondary" onClick={onRefresh} disabled={loading}><I name="refresh" />刷新报告列表</button>
            </div>
            {reportList.length ? (
              <div className="capability-list" style={{ marginTop: 18 }}>
                {reportList.map((item) => (
                  <button className="capability-row" key={item.report_id} onClick={() => onOpen(item.report_id, item.run_id)}>
                    <span className="cap-icon cap-skill"><I name="doc" /></span>
                    <div>
                      <strong className="mono">{item.report_id}</strong>
                      <span className="muted">Run {item.run_id} / {item.created_at}</span>
                    </div>
                    <span className="row-tail"><I name="arrowRight" /></span>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState icon="doc" title="尚未选择服务端报告" detail="完成一次 Run 并生成 Product Evaluation Report，或导入已有的报告 JSON。" />
            )}
          </section>
        </div>
      </Page>
    );
  }

  const decisionKey = (gate?.decision || "pending").toLowerCase();
  const decisionClass = ["block", "approve", "review"].includes(decisionKey) ? decisionKey : "pending";
  const findings = report.findings || [];
  const recommendations = report.recommendations || [];
  const scenarios = report.scenario_stability?.scenarios || [];
  const dimensions = report.executive_summary?.dimensions || [];
  const limitations = report.limitations || [];
  const evidenceConditions = evidence?.conditions || [];
  const evidenceRecords = evidence?.records || [];
  const evidenceSummary = evidence?.summary || {};

  return (
    <Page
      title={report.subject?.component_name || report.report_id}
      kicker="产品评估报告 · PRODUCT EVALUATION REPORT"
      intro={report.product_overview?.why_it_exists || report.evaluation?.question || "Validated Product Evaluation Report"}
    >
      {/* Release decision band */}
      <div className={`decision-band d-${decisionClass}`}>
        <div className="decision-info">
          <span className="eyebrow">Release Decision · 发布决策</span>
          <strong className={`decision ${decisionClass}`}>{gate?.decision || "pending"}</strong>
          <div className="decision-copy">{gate?.rationale || "报告已加载，但尚未经过确定性 Gate 评估。"}</div>
          <div className="button-row">
            <button className="secondary" onClick={() => exportReport("json")}><I name="download" />JSON</button>
            <button className="secondary" onClick={() => exportReport("md")}><I name="download" />Markdown</button>
            <button className="secondary" onClick={() => exportReport("html")}><I name="download" />HTML</button>
            <FileButton id="report-import-existing" accept="application/json,.json" onChange={onImport}>替换报告</FileButton>
          </div>
        </div>
        <div className="decision-visual"><DecisionShield decision={gate?.decision} /></div>
      </div>

      {/* Summary metrics */}
      <section className="summary-grid">
        <Metric label="报告状态" value={report.status} />
        <Metric label="Evidence" value={report.evaluation?.evidence_status || "unknown"} />
        <Metric label="场景数" value={scenarios.length || "-"} />
        <Metric label="Findings" value={findings.length || "-"} />
        <Metric label="Report hash" value={String(report.report_hash || "").slice(0, 12) || "-"} />
      </section>

      <div className="report-sections">
        <div className="stack">
          {/* Structured Evidence Bundle */}
          <section className="section-block">
            <SectionHeading label="Evidence Bundle" title={evidenceConditions.length ? `${evidenceConditions.length} conditions` : "Structured execution evidence"} />
            {evidenceConditions.length ? (
              <>
                <div className="summary-grid evidence-summary-grid">
                  <Metric label="Verified" value={evidenceSummary.verified_condition_count ?? "-"} />
                  <Metric label="Passed" value={evidenceSummary.passed_condition_count ?? "-"} />
                  <Metric label="Failed" value={evidenceSummary.failed_condition_count ?? "-"} />
                  <Metric label="Cost" value={Number.isFinite(Number(evidenceSummary.total_cost_usd)) ? `$${Number(evidenceSummary.total_cost_usd).toFixed(6)}` : "-"} />
                </div>
                <div className="stack-tight">
                  {evidenceConditions.map((condition) => <EvidenceCondition key={condition.condition_id} condition={condition} records={evidenceRecords} />)}
                </div>
              </>
            ) : <p className="muted">No structured Evidence Bundle conditions are attached to this report.</p>}
          </section>

          {/* Gate checks */}
          <section className="section-block">
            <SectionHeading label="Release Gate" title="确定性 Gate 检查项" />
            <div className="stack-tight">
              {gate?.checks?.map((check) => (
                <div className="check-row" key={check.name}>
                  <Status value={check.status} />
                  <strong>{check.name.replaceAll("_", " ")}</strong>
                  <span>{check.detail}</span>
                </div>
              ))}
              {!gate?.checks?.length && <p className="muted">暂无 Gate 检查记录。</p>}
            </div>
          </section>

          {/* Interaction analysis */}
          {interaction && (
            <section className="section-block">
              <SectionHeading label="Interaction Analysis" title="组件关系变化分析" />
              <p className="large-copy">{interaction.summary}</p>
              <div className="interaction-grid">
                {Object.entries(interaction)
                  .filter(([key, value]) => key !== "summary" && typeof value === "string")
                  .map(([key, value]) => (
                    <div key={key}>
                      <span className="eyebrow">{key.replaceAll("_", " ")}</span>
                      <p>{value}</p>
                    </div>
                  ))}
              </div>
            </section>
          )}

          {/* Findings */}
          {findings.length > 0 && (
            <section className="section-block">
              <SectionHeading label="Findings" title={`关键发现 · ${findings.length} 项`} />
              <div className="stack-tight">
                {findings.map((finding) => {
                  const sev = String(finding.severity || "none").toLowerCase();
                  return (
                    <div className={`finding-card sev-${sev}`} key={finding.finding_id}>
                      <div className="finding-head">
                        <span className={`sev-badge sev-${sev}`}>{finding.severity || "info"}</span>
                        <strong>{finding.finding_id}</strong>
                        <span className="component-type">{finding.finding_type}{finding.impact_dimension ? ` · ${finding.impact_dimension}` : ""}</span>
                      </div>
                      <p>{finding.observation}</p>
                      {finding.product_meaning && <p className="muted">{finding.product_meaning}</p>}
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* Scenario stability */}
          {scenarios.length > 0 && (
            <section className="section-block">
              <SectionHeading label="Scenario Stability" title={`场景稳定性 · ${scenarios.length} 个场景`} />
              {report.scenario_stability?.summary && <p className="large-copy">{report.scenario_stability.summary}</p>}
              <div className="stack-tight">
                {scenarios.map((scenario) => (
                  <div className="check-row" key={scenario.scenario_id}>
                    <Status value={scenario.status || "review"} />
                    <strong>{scenario.name || scenario.scenario_id}</strong>
                    <span>{scenario.result || scenario.observation || ""}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="stack">
          {/* Conclusion */}
          <section className="section-block">
            <SectionHeading label="Conclusion" title="分析结论" />
            <p className="large-copy">{report.executive_summary?.final_conclusion || report.product_overview?.why_it_exists || "未提供执行结论。"}</p>
            {(report.executive_summary?.product_recommendation || report.recommendations?.[0]?.action) && (
              <p className="muted">{report.executive_summary?.product_recommendation || report.recommendations?.[0]?.action}</p>
            )}
            {dimensions.length > 0 && (
              <div className="stack-tight" style={{ marginTop: 16 }}>
                {dimensions.map((dim, index) => (
                  <div className="check-row" key={dim.dimension || index}>
                    <Status value={dim.status || dim.conclusion || "review"} />
                    <strong>{dim.dimension}</strong>
                    <span>{dim.conclusion}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Recommendations */}
          {recommendations.length > 0 && (
            <section className="section-block">
              <SectionHeading label="Recommendations" title="建议行动" />
              <div className="stack-tight">
                {recommendations.map((reco, index) => (
                  <div className="reco-item" key={reco.recommendation_id || index}>
                    <span className="reco-index">{index + 1}</span>
                    <p><strong>{reco.target || reco.priority || "action"}</strong>{reco.action}{reco.reasoning ? ` — ${reco.reasoning}` : ""}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Limitations */}
          {limitations.length > 0 && (
            <section className="section-block">
              <SectionHeading label="Limitations" title="评估边界与限制" />
              <div className="stack-tight">
                {limitations.map((item, index) => (
                  <div className="check-row" key={index}>
                    <I name="alert" />
                    <span>{item.statement}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Technical metadata */}
          <section className="section-block">
            <SectionHeading label="Metadata" title="技术元数据" />
            <dl className="definition-list">
              <div><dt>Report ID</dt><dd className="mono">{report.report_id}</dd></div>
              <div><dt>组件</dt><dd>{report.subject?.component_type} / {report.subject?.component_name}</dd></div>
              <div><dt>评估类型</dt><dd>{report.evaluation_type || report.evaluation?.evaluation_type || "-"}</dd></div>
              <div><dt>Schema</dt><dd className="mono">{report.schema_version || "-"}</dd></div>
              <div><dt>Report hash</dt><dd className="mono">{String(report.report_hash || "-").slice(0, 20)}</dd></div>
            </dl>
          </section>

          <ReportHistory reportList={reportList} onOpen={onOpen} onRefresh={onRefresh} loading={loading} />
        </div>
      </div>
    </Page>
  );
}
