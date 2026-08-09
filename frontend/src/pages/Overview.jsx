import React, { useState } from "react";
import { I, CapIcon, FileButton, Status, Page, SectionHeading, EmptyState, statusTone } from "../components.jsx";
import { pathFor, projectDisplayName, projectPurpose } from "../lib.js";

const DEMO_SOURCE = "__demo_lighttable__";
const READ_ONLY_NOTICE = "此项目仅供示意，不可编辑。请上传正式 Project 后执行此操作。";

const DIFF_META = {
  added: { label: "新增", hint: "检测到新增能力" },
  changed: { label: "已变更", hint: "检测到能力变化" },
  removed: { label: "已移除", hint: "检测到能力移除" },
};

const WORKFLOW = [
  ["branch", "Change", "变更感知"],
  ["layers", "Capability", "能力映射"],
  ["evaluation", "Evaluation", "定向评测"],
  ["file-text", "Evidence", "证据收集"],
  ["shield-check", "Decision", "发布决策"],
];

function short(value, length = 28) {
  const text = String(value || "-");
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function date(value) {
  if (!value) return "暂无时间";
  return String(value).replace("T", " ").replace(/\.\d+Z$/, "").replace(/\+00:00$/, " UTC");
}

function displayStatus(intelligence, gate, report) {
  const decision = String(gate?.decision || "").toLowerCase();
  if (decision === "block") return "BLOCKED";
  if (decision === "approve") return "PASS";
  if (decision === "review") return "REVIEW";
  if (String(report?.status || "").toLowerCase() === "completed") return "PASS";
  return String(intelligence?.status || "review").toUpperCase();
}

function progressSteps({ intelligence, report, plan, run }) {
  return [
    { label: "环境检查", state: intelligence?.runtime_profile ? "complete" : "pending" },
    { label: "变更检测", state: intelligence?.latest_diff ? "complete" : "pending" },
    { label: "测试选择", state: plan ? "complete" : "pending" },
    { label: "Agent 执行", state: run?.status === "completed" ? "complete" : run ? "running" : "pending" },
    { label: "能力评估", state: report ? "complete" : "pending" },
    { label: "报告生成", state: report ? "complete" : "pending" },
  ];
}

function ProjectHeader({ displayName, manifest, baseline, latest, runtime, status, onDetail }) {
  return <section className="project-header-card">
    <div className="project-mark"><I name="cube" size={28} /></div>
    <div className="project-header-copy">
      <span className="eyebrow">Project</span>
      <h2>{displayName}</h2>
      <p>{projectPurpose(manifest, displayName)}</p>
    </div>
    <div className="project-header-meta">
      <div><span>Baseline</span><strong title={baseline?.baseline_version}>{short(baseline?.baseline_version, 20)}</strong></div>
      <div><span>Candidate</span><strong title={latest?.version}>{short(latest?.version, 20)}</strong></div>
      <div><span>Runtime</span><strong>{runtime?.runtime_kind || "-"}</strong></div>
    </div>
    <div className="project-header-actions"><Status value={status} /><button className="secondary" onClick={onDetail}><I name="cube" />项目详情<I name="arrowRight" /></button></div>
  </section>;
}

function ProgressCard({ steps }) {
  const completed = steps.filter((item) => item.state === "complete").length;
  const current = steps.findIndex((item) => item.state === "running");
  const percent = Math.round((completed / steps.length) * 100);
  return <section className="dashboard-card progress-card">
    <div className="card-heading-row"><div><span className="eyebrow">Evaluation Progress</span><h2>评估进度</h2></div><strong className="progress-percent">{current >= 0 ? Math.max(1, Math.round(((current + 0.5) / steps.length) * 100)) : percent}%</strong></div>
    <div className="vertical-stepper">
      {steps.map((step, index) => <div className={`vertical-step ${step.state}`} key={step.label}>
        <span className="step-orb">{step.state === "complete" ? <I name="check" size={13} /> : step.state === "running" ? <span className="step-dot" /> : <span />}</span>
        <span className="step-name">{step.label}</span>
        <span className="step-state">{step.state === "complete" ? "100%" : step.state === "running" ? "进行中" : "Pending"}</span>
      </div>)}
    </div>
  </section>;
}

function MetricCell({ label, value, tone = "" }) {
  return <div className={`metric-cell ${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

export default function Overview({ projectId, intelligence, scans, uploads, benchmarks = [], reportList = [], report, gate, onNew, onDetail, onExitProject, onReport, onUploaded, onProjectIdChange, request, refreshProject, setLoading, setNotice, demoMode = false, onDemoLoad }) {
  const [version, setVersion] = useState("candidate");
  const [selectedUpload, setSelectedUpload] = useState("");
  const [selectedFilename, setSelectedFilename] = useState("");
  const [sourceKind, setSourceKind] = useState("package");
  const latestReport = report || reportList?.[0];
  const latestReportMeta = reportList?.[0] || latestReport;

  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedFilename(file.name);
    if (!projectId) { setNotice({ kind: "error", text: "请先输入新的 Project ID，再上传项目文件夹。" }); event.target.value = ""; return; }
    if (demoMode) { setNotice({ kind: "error", text: READ_ONLY_NOTICE }); event.target.value = ""; return; }
    setLoading(true);
    try {
      const data = new FormData(); data.append("file", file); data.append("source_kind", "package");
      const result = await request(pathFor(projectId, "/uploads"), { method: "POST", body: data });
      setSelectedUpload(result.source_ref); onUploaded?.(result); setNotice({ kind: "success", text: `项目文件已存储为 ${result.source_ref}。` });
    } catch (error) { setNotice({ kind: "error", text: error.message }); }
    finally { setLoading(false); event.target.value = ""; }
  };

  const scan = async () => {
    if (selectedUpload === DEMO_SOURCE) { onDemoLoad?.(); return; }
    if (demoMode) { setNotice({ kind: "error", text: READ_ONLY_NOTICE }); return; }
    if (!selectedUpload || !projectId) { setNotice({ kind: "error", text: "请先上传项目文件并选择对应的 Source。" }); return; }
    setLoading(true);
    try {
      const result = await request(pathFor(projectId, "/scan"), { method: "POST", body: JSON.stringify({ source_kind: sourceKind, source_ref: selectedUpload, version: version.trim() }) });
      if (result.intelligence) await refreshProject(projectId);
      const unresolved = result.scan.unresolved_reasons?.join(" ");
      setNotice({ kind: result.scan.status === "ready" ? "success" : "error", text: result.scan.status === "ready" ? "扫描完成，项目已更新。" : `扫描状态：${result.scan.status}${unresolved ? ` · ${unresolved}` : ""}` });
    } catch (error) { setNotice({ kind: "error", text: error.message }); }
    finally { setLoading(false); }
  };

  if (!intelligence) {
    return <Page title="连接一个 Agent Project" kicker="项目接入 · PROJECT INTAKE" intro="上传项目文件并完成扫描，系统会创建首个 Project Intelligence。">
      <section className="intake-card">
        <div className="intake-visual"><span className="intake-icon"><I name="upload" size={28} /></span><h2>上传并扫描项目</h2><p>也可以从已存储的 Source 直接进入 LightTable 示例项目。</p></div>
        <div className="intake-form">
          <label className="field"><span>项目 ID</span><input value={projectId} onChange={(event) => onProjectIdChange?.(event.target.value)} placeholder="例如 my-agent-project" autoComplete="off" /></label>
          <div className="field"><span>上传项目文件</span><div className="button-row compact"><FileButton id="project-source-upload-first" accept=".zip,.tar,.gz,application/zip" onChange={upload}>选择项目文件</FileButton>{selectedFilename && <span className="form-hint">已选择：{selectedFilename}</span>}</div></div>
          <label className="field"><span>已存储的 Source</span><select value={selectedUpload} onChange={(event) => { const value = event.target.value; setSelectedUpload(value); if (value === DEMO_SOURCE) onDemoLoad?.(); }}><option value="">选择一个上传记录</option><option value={DEMO_SOURCE}>LightTable</option>{uploads.map((item) => <option key={item.upload_id} value={item.source_ref}>{item.original_filename} / {item.source_ref}</option>)}</select></label>
          <div className="intake-bottom"><label className="field"><span>初始版本号</span><input value={version} onChange={(event) => setVersion(event.target.value)} /></label><button className="primary scan-create-button" onClick={scan}><I name="radar" />扫描并创建项目<I name="arrowRight" /></button></div>
        </div>
      </section>
    </Page>;
  }

  const { agent_manifest: manifest = {}, baseline_snapshot: baseline = {}, latest_snapshot: latest = baseline, runtime_profile: runtime = {} } = intelligence;
  const displayName = projectDisplayName(manifest, projectId);
  const registry = intelligence.capability_registry || [];
  const diffChanges = intelligence.latest_diff?.component_changes || [];
  const status = displayStatus(intelligence, gate, latestReport);
  const steps = progressSteps({ intelligence, report: latestReport, plan: null, run: null });
  const changed = diffChanges.length ? diffChanges : registry.slice(0, 4).map((item) => ({ component_type: item.component_type, component_name: item.name, status: "changed", responsibility: item.responsibility }));
  const history = [
    { label: "Baseline", version: baseline.baseline_version, status: "ready", time: scans?.find((item) => item.version === baseline.baseline_version)?.created_at, findings: scans?.find((item) => item.version === baseline.baseline_version)?.findings?.length || 0 },
    { label: "Candidate", version: latest.version, status: intelligence.latest_diff ? "review" : intelligence.status, time: scans?.find((item) => item.version === latest.version)?.created_at, findings: scans?.find((item) => item.version === latest.version)?.findings?.length || 0 },
    { label: "Latest Evaluated", version: latestReport?.report_id || "尚未评估", status: latestReport ? "pass" : "pending", time: latestReportMeta?.created_at, findings: latestReport?.findings?.length || latestReport?.main_findings?.length || 0 },
  ];
  const summary = latestReport?.evaluation_results?.summary || latestReport?.evidence?.summary || {};
  const metrics = [
      ["Success", latestReport ? (summary.failure_rate === 0 ? "PASS" : summary.failure_rate == null ? "REVIEW" : `${Math.round((1 - summary.failure_rate) * 100)}%`) : "Pending", latestReport ? "ok" : ""],
      ["Capability", latestReport?.executive_summary?.status || (intelligence.latest_diff ? "CHANGED" : "READY"), "violet"],
      ["Safety", gate?.decision === "block" ? "BLOCKED" : "PASS", gate?.decision === "block" ? "bad" : "ok"],
      ["Cost", summary.total_cost_usd != null ? `$${Number(summary.total_cost_usd).toFixed(4)}` : "-", ""],
      ["Decision", gate?.decision?.toUpperCase() || (latestReport ? "REVIEW" : "PENDING"), gate?.decision === "block" ? "bad" : ""],
    ];

  return <Page title="总览" kicker="项目工作台 · OVERVIEW" intro="按项目扫描、能力变化与评估证据查看当前状态。" action={<div className="overview-page-actions"><button className="secondary" onClick={onExitProject}><I name="x" />退出项目<I name="arrowRight" /></button><button className="primary" onClick={() => onNew(null)}><I name="flask" />新建评估</button></div>}>
    <ProjectHeader displayName={displayName} manifest={manifest} baseline={baseline} latest={latest} runtime={runtime} status={status} onDetail={onDetail} />
    <div className="overview-top-grid">
      <section className="dashboard-card status-hero-card"><div className="status-hero-copy"><span className="eyebrow">Current Status</span><h2>当前状态</h2><strong className={`hero-status status-word-${status.toLowerCase()}`}>{status}</strong><p>{status === "BLOCKED" ? "当前版本暂不可通过评估" : status === "PASS" ? "最近一次评估已通过当前检查" : "项目已完成扫描，等待下一步评估"}</p><div className="hero-facts"><span><I name="branch" />{short(baseline.baseline_version, 18)} → {short(latest.version, 18)}</span><span><I name="server-gear" />{runtime.runtime_kind || "-"}</span></div></div><div className="status-hero-art"><I name={status === "PASS" ? "shield-check" : "shield-alert"} size={124} /></div></section>
      <ProgressCard steps={steps} />
    </div>
    <section className="dashboard-card latest-evaluation-card"><div className="latest-intro"><span className="eyebrow">Latest Evaluation</span><h2>最近一次评估</h2><div className="version-pair"><strong>{short(baseline.baseline_version, 20)}</strong><I name="arrowRight" size={16} /><strong>{short(latest.version, 20)}</strong></div><p>{latestReport ? date(latestReportMeta?.created_at) : "尚未生成报告"}</p><span className="latest-summary">{latestReport?.executive_summary?.final_conclusion || latestReport?.product_overview?.why_it_exists || "当前项目已有扫描结果，可从新建评估开始生成 Evidence。"}</span></div><div className="metric-cells">{metrics.map(([label, value, tone]) => <MetricCell key={label} label={label} value={value} tone={tone} />)}</div></section>
    <div className="overview-middle-grid"><section className="dashboard-card history-card"><div className="card-heading-row"><div><span className="eyebrow">Agent Projects</span><h2>版本 / 扫描历史</h2></div><span className="card-count">{scans?.length || 0} 次扫描</span></div><div className="history-list">{history.map((item) => <div className="history-row" key={item.label}><span className="history-icon"><I name={item.label === "Baseline" ? "branch" : item.label === "Candidate" ? "sparkle" : "file-text"} size={18} /></span><div className="history-main"><strong>{item.label}</strong><span title={item.version}>{short(item.version, 30)}</span></div><div className="history-meta"><Status value={item.status} /><span>{date(item.time)}</span><span>{item.findings} findings</span></div></div>)}{!scans?.length && <p className="muted">暂无扫描记录。</p>}</div></section><section className="dashboard-card quick-actions-card"><div className="card-heading-row"><div><span className="eyebrow">Quick Actions</span><h2>快捷操作</h2></div></div><div className="quick-actions-grid"><button onClick={() => onNew(null)}><I name="flask" size={24} /><span>新建评估</span><small>New Evaluation</small></button><FileButton id="project-source-upload-quick" accept=".zip,.tar,.gz,application/zip" onChange={upload} icon="upload">上传候选版本</FileButton><button onClick={() => latestReport && onReport?.(latestReport.report_id, latestReport.run_id)} disabled={!latestReport}><I name="file-text" size={24} /><span>查看评估报告</span><small>Evaluation Report</small></button><button onClick={onDetail}><I name="provider-orbit" size={24} /><span>配置 Provider</span><small>Configure Provider</small></button></div></section></div>
    <section className="dashboard-card capability-changes-card"><div className="card-heading-row"><div><span className="eyebrow">Capability Changes</span><h2>能力变化</h2></div><span className="card-count">{changed.length} 项</span></div><div className="compact-capability-grid">{changed.slice(0, 6).map((item) => { const meta = DIFF_META[item.status] || DIFF_META.changed; return <button className="compact-capability-card" key={`${item.component_type}:${item.component_name}`} onClick={() => onNew({ componentType: item.component_type, componentName: item.component_name, changeType: item.status === "added" ? "add" : item.status === "removed" ? "remove" : "modify" })}><CapIcon type={item.component_type} /><div><strong>{item.component_name}</strong><span>{item.component_type === "skill_pair" ? "Skill Pair" : item.component_type === "tool" ? "Tool" : "Skill"}</span><p>{meta.hint}</p></div><Status value={meta.label} /></button>; })}</div></section>
    <div className="workflow-strip">{WORKFLOW.map(([icon, en, zh], index) => <React.Fragment key={en}><div className="workflow-node"><span className="wf-icon"><I name={icon} size={18} /></span><strong>{en}</strong><small>{zh}</small></div>{index < WORKFLOW.length - 1 && <I name="arrowRight" size={16} className="workflow-arrow" />}</React.Fragment>)}</div>
    <section className="intake-card overview-intake-card"><div><span className="eyebrow">Project Intake</span><h2>上传候选版本</h2><p>保留当前真实上传、Source 选择与扫描功能；示例项目为只读。</p></div><div className="intake-form intake-form-inline"><div className="field"><span>已存储的 Source</span><select value={selectedUpload} onChange={(event) => { const value = event.target.value; setSelectedUpload(value); if (value === DEMO_SOURCE) onDemoLoad?.(); }}><option value="">选择一个上传记录</option><option value={DEMO_SOURCE}>LightTable</option>{uploads.map((item) => <option key={item.upload_id} value={item.source_ref}>{item.original_filename} / {short(item.source_ref, 30)}</option>)}</select></div><div className="field"><span>候选版本号</span><input value={version} onChange={(event) => setVersion(event.target.value)} /></div><button className="secondary" onClick={scan}><I name="radar" />扫描所选 Source</button></div></section>
  </Page>;
}
