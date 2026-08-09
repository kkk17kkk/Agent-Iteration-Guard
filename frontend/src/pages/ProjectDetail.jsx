import React, { useEffect, useState } from "react";
import { I, CapIcon, FileButton, Status, Page, SectionHeading, EmptyState, Metric, ProjectContextCard } from "../components.jsx";
import { projectDisplayName } from "../lib.js";

const TABS = [
  ["overview", "总览"],
  ["versions", "版本"],
  ["knowledge", "知识"],
  ["benchmarks", "Benchmark"],
  ["experiments", "评估历史"],
  ["configuration", "配置"],
];

function formatDate(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").replace(/\.\d+Z$/, " UTC");
}

export default function ProjectDetail({ projectId, intelligence, projectHeader, knowledge = [], benchmarks = [], reportList = [], providers = [], executionConfigs = [], onNew, onReport, onOverview, onImportBenchmark, demoMode = false, readOnlyNotice }) {
  const [activeTab, setActiveTab] = useState("overview");
  const controlProviders = providers.filter((item) => item.role === "control_plane");
  const [selectedProviderId, setSelectedProviderId] = useState(controlProviders[0]?.provider_binding_id || "");
  const [selectedBenchmarkFilename, setSelectedBenchmarkFilename] = useState("");

  useEffect(() => {
    if (!controlProviders.some((item) => item.provider_binding_id === selectedProviderId)) {
      setSelectedProviderId(controlProviders[0]?.provider_binding_id || "");
    }
  }, [providers, selectedProviderId]);

  if (!intelligence) {
    return <EmptyState icon="cube" title="请先加载项目" detail="Project Detail 需要 Project Intelligence。请在右上角输入已注册项目 ID 并加载。" action="返回项目概览" onClick={onOverview} />;
  }

  const manifest = intelligence.agent_manifest || {};
  const baseline = intelligence.baseline_snapshot || {};
  const latest = intelligence.latest_snapshot || baseline;
  const runtime = intelligence.runtime_profile || {};
  const displayName = projectDisplayName(manifest, projectId);
  const registry = intelligence.capability_registry || [];
  const latestReport = reportList[0];
  const latestDiff = intelligence.latest_diff?.component_changes || [];
  const skillCount = registry.filter((item) => item.component_type === "skill").length;
  const pairCount = registry.filter((item) => item.component_type === "skill_pair").length;
  const toolCount = registry.filter((item) => item.component_type === "tool").length;

  const renderTab = () => {
    if (activeTab === "versions") {
      return (
        <section className="section-block">
          <SectionHeading label="Versions" title="已注册版本与变更" />
          <div className="version-summary">
            <div className="version-card"><span className="eyebrow">Baseline</span><strong>{baseline.baseline_version || "-"}</strong><span className="muted">生产基线</span></div>
            <div className="version-arrow-large">→</div>
            <div className="version-card"><span className="eyebrow">Latest candidate</span><strong>{latest.version || "-"}</strong><span className="muted">当前候选 Snapshot</span></div>
          </div>
          <div className="capability-list" style={{ marginTop: 18 }}>
            {(intelligence.snapshot_history || []).map((snapshot) => (
              <div className="capability-row" key={snapshot.snapshot_id || snapshot.version}>
                <span className="cap-icon cap-skill"><I name="branch" /></span>
                <div><strong>{snapshot.version}</strong><span className="muted">{snapshot.snapshot_id} · {snapshot.runtime_profile?.runtime_kind || runtime.runtime_kind || "runtime"}</span></div>
                <span className="row-tail"><Status value={snapshot.version === latest.version ? "latest" : "registered"} /></span>
              </div>
            ))}
            {!intelligence.snapshot_history?.length && <p className="muted">暂无版本历史。</p>}
          </div>
        </section>
      );
    }

    if (activeTab === "knowledge") {
      return (
        <section className="section-block">
          <SectionHeading label="Project Knowledge" title="评估知识与来源" />
          <p className="form-hint">知识用于下一次 Planner 的覆盖提示，不等同于 Ground Truth；每条记录保留来源评估与 Evidence 引用。</p>
          <div className="knowledge-grid">
            {knowledge.map((item) => (
              <article className="knowledge-card" key={item.knowledge_id || item.component_pattern}>
                <div className="diff-head"><CapIcon type={item.component_pattern?.includes("pair") ? "skill_pair" : "skill"} /><strong>{item.component_pattern}</strong><Status value={item.evidence_level || "observed"} /></div>
                <div className="chip-row">{(item.common_risks || []).map((risk) => <span className="chip" key={risk}>{risk}</span>)}</div>
                <p className="muted">{item.sample_count || 0} 个样本 · {item.source_evaluation_ids?.length || 0} 个来源评估 · 更新于 {formatDate(item.updated_at)}</p>
              </article>
            ))}
            {!knowledge.length && <p className="muted">当前项目还没有可复用的 Evaluation Knowledge。</p>}
          </div>
        </section>
      );
    }

    if (activeTab === "benchmarks") {
      const importBenchmark = async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        setSelectedBenchmarkFilename(file.name);
        await onImportBenchmark?.(file);
        event.target.value = "";
      };
      return (
        <section className="section-block">
          <SectionHeading label="Benchmark Evidence" title="导入与管理外部 Benchmark 结果" />
          <p className="form-hint">支持导入 Benchmark summary JSON。只保存结果摘要和完整性信息，不执行外部 Benchmark，也不替代 AIG 本地 Oracle 证据。</p>
          <div className="button-row benchmark-import-row">
            <FileButton id="benchmark-result-upload" accept=".json,application/json" onChange={importBenchmark}>选择 Benchmark JSON</FileButton>
            {selectedBenchmarkFilename && <span className="form-hint">已选择：{selectedBenchmarkFilename}</span>}
          </div>
          <div className="benchmark-grid" style={{ marginTop: 18 }}>
            {benchmarks.map((item) => (
              <article className="benchmark-card" key={item.evidence_id}>
                <div className="diff-head"><I name="beaker" /><strong>{item.benchmark_name}</strong><Status value={item.evidence_level || "external"} /></div>
                <div className="benchmark-metrics">
                  {(item.metrics || []).map((metric) => <span className="chip" key={`${item.evidence_id}:${metric.metric_name}`}>{metric.metric_name}: {metric.baseline_value} → {metric.candidate_value} {metric.unit}</span>)}
                </div>
                <p className="form-hint">Source: <span className="mono">{item.source_ref}</span></p>
                <p className="form-hint">SHA256: <span className="mono">{item.source_sha256}</span></p>
              </article>
            ))}
            {!benchmarks.length && <p className="muted">尚未导入 Benchmark 结果。</p>}
          </div>
        </section>
      );
    }

    if (activeTab === "experiments") {
      return (
        <section className="section-block">
          <SectionHeading label="Experiments" title="评估与报告历史" />
          <div className="capability-list">
            {reportList.map((item) => (
              <button className="capability-row" key={item.report_id} onClick={() => onReport?.(item.report_id, item.run_id)}>
                <span className="cap-icon cap-skill"><I name="flask" /></span>
                <div><strong className="mono">{item.report_id}</strong><span className="muted">Run {item.run_id} · {formatDate(item.created_at)}</span></div>
                <span className="row-tail"><I name="arrowRight" /></span>
              </button>
            ))}
            {!reportList.length && <p className="muted">暂无已持久化的评估报告。</p>}
          </div>
        </section>
      );
    }

    if (activeTab === "configuration") {
      const selectedProvider = controlProviders.find((item) => item.provider_binding_id === selectedProviderId);
      return (
        <section className="section-block">
          <SectionHeading label="Configuration" title="Provider 与执行配置" />
          <p className="form-hint">API Key 不在 GUI 中输入、显示或保存；后端从项目根目录 `.env` / 运行环境读取 secret。这里可以在多个已注册 binding 之间切换本次评测使用的配置。</p>
          <div className="configuration-grid">
            <div className="form-panel">
              <h3>Provider bindings</h3>
              <label className="field">当前 Control-plane Binding
                  <select value={selectedProviderId} onChange={(event) => { if (demoMode) { readOnlyNotice?.(); return; } setSelectedProviderId(event.target.value); }}>
                  <option value="">选择一个 control-plane binding</option>
                  {controlProviders.map((item) => <option key={item.provider_binding_id} value={item.provider_binding_id}>{item.provider} / {item.model} / {item.status} · {item.provider_binding_id.slice(-6)}</option>)}
                </select>
              </label>
              {selectedProvider && <div className="provider-binding-facts">
                <span className="eyebrow">Selected binding metadata</span>
                <span>{selectedProvider.provider} / {selectedProvider.model} · {selectedProvider.status}</span>
                <span>env: {selectedProvider.expected_environment_variable || "未返回环境变量名"}</span>
                <span>hosts: {(selectedProvider.allowed_hosts || []).join(", ") || "-"}</span>
                <span>budget: ${selectedProvider.batch_budget_usd ?? "-"} · timeout: {selectedProvider.timeout_seconds ?? "-"}s · calls: {selectedProvider.max_model_calls ?? "-"}</span>
              </div>}
              <button className="secondary" style={{ marginTop: 14 }} disabled={!selectedProviderId} onClick={() => onNew?.({ providerBindingId: selectedProviderId })}><I name="flask" />用于新建评估</button>
              <div style={{ marginTop: 14 }}>{providers.map((item) => <div className="config-row" key={item.provider_binding_id}><strong>{item.provider} / {item.model} · {item.provider_binding_id.slice(-6)}</strong><span>{item.role} · {item.status} · {item.expected_environment_variable || "env 未返回"}</span></div>)}</div>
              {!providers.length && <p className="muted">暂无 Provider Binding。</p>}
            </div>
            <div className="form-panel"><h3>Execution configurations</h3>{executionConfigs.map((item) => <div className="config-row" key={item.config_id}><strong>{item.name}</strong><span>{item.oracle_id} · {item.status || "registered"}</span></div>)}{!executionConfigs.length && <p className="muted">暂无执行配置。</p>}</div>
          </div>
        </section>
      );
    }

    return (
      <div className="project-detail-grid">
        <section className="section-block project-overview-panel">
          <SectionHeading label="Current Overview" title="当前项目状态" />
          <div className="summary-grid">
            <Metric label="Release status" value={intelligence.status} />
            <Metric label="Version" value={`${baseline.baseline_version || "-"} → ${latest.version || "-"}`} />
            <Metric label="Latest result" value={latestReport?.report_id || "not evaluated"} />
            <Metric label="Last evaluation" value={latestReport ? formatDate(latestReport.created_at) : "-"} />
            <Metric label="Benchmark results" value={benchmarks.length} />
          </div>
          <div className="definition-list">
            <div><dt>Project ID</dt><dd className="mono">{projectId}</dd></div>
            <div><dt>Runtime</dt><dd>{runtime.runtime_kind || "-"} · <span className="mono">{runtime.entrypoint || "-"}</span></dd></div>
            <div><dt>Detected changes</dt><dd>{latestDiff.length} component changes</dd></div>
          </div>
        </section>
        <section className="section-block">
          <SectionHeading label="Capabilities" title="能力组件" />
          <div className="capability-counts"><Metric label="Skills" value={skillCount} /><Metric label="Skill Pairs" value={pairCount} /><Metric label="Tools" value={toolCount} /></div>
          <div className="chip-row" style={{ marginTop: 16 }}>{registry.slice(0, 8).map((item) => <span className="chip" key={item.capability_id || item.name}><CapIcon type={item.component_type} />{item.name}</span>)}</div>
        </section>
        <section className="section-block">
          <SectionHeading label="Project Knowledge" title="知识摘要" />
          <div className="knowledge-summary"><strong>{knowledge.length}</strong><span>条有来源的 Evaluation Knowledge</span></div>
          {knowledge[0] && <p className="large-copy">最新模式：{knowledge[0].component_pattern}</p>}
          <button className="secondary" onClick={() => setActiveTab("knowledge")}>打开 Knowledge<I name="arrowRight" /></button>
        </section>
      </div>
    );
  };

  return (
    <Page
      title="项目详情"
      kicker="项目详情 · PROJECT DETAIL"
      intro="查看当前项目的版本、知识、评估历史与配置。"
      action={<div className="button-row" style={{ marginTop: 0 }}><button className="primary" onClick={() => onNew?.(null)}><I name="play" />开始评估</button><button className="secondary" onClick={() => latestReport && onReport?.(latestReport.report_id, latestReport.run_id)} disabled={!latestReport}><I name="file" />查看最新报告</button></div>}
      headingCard
      before={<ProjectContextCard
        {...projectHeader}
        onOverview={onOverview}
        onVersions={() => setActiveTab("versions")}
        onConfiguration={() => setActiveTab("configuration")}
        actions
      />}
    >
      <div className="project-tabs" role="tablist" aria-label="Project detail sections">
        {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={activeTab === id} className={activeTab === id ? "project-tab active" : "project-tab"} onClick={() => setActiveTab(id)}>{label}</button>)}
      </div>
      <div className="project-detail-content">{renderTab()}</div>
    </Page>
  );
}
