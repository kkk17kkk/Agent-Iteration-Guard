import React, { useState } from "react";
import { I, CapIcon, FileButton, Status, Page, SectionHeading, EmptyState, PipelineStep, statusTone } from "../components.jsx";
import { pathFor, projectDisplayName } from "../lib.js";

const DIFF_META = {
  added: { label: "新增组件", hint: "检测到新增能力组件，建议评估新增能力", changeType: "add", tone: "ok" },
  changed: { label: "已变更", hint: "检测到组件变化，建议执行回归评测", changeType: "modify", tone: "warn" },
  removed: { label: "已移除", hint: "检测到组件移除，建议验证能力损失", changeType: "remove", tone: "bad" },
};

const TYPE_FILTERS = [
  ["all", "全部"],
  ["skill", "Skill"],
  ["skill_pair", "Skill Pair"],
  ["tool", "Tool"],
];

const WORKFLOW = [
  ["branch", "Change", "变更感知"],
  ["layers", "Capability", "能力映射"],
  ["flask", "Evaluation", "定向评测"],
  ["doc", "Evidence", "证据收集"],
  ["shield", "Decision", "发布决策"],
];

export default function Overview({ projectId, intelligence, scans, uploads, benchmarks = [], onNew, onDetail, onUploaded, onProjectIdChange, request, refreshProject, setLoading, setNotice }) {
  const [version, setVersion] = useState("candidate");
  const [selectedUpload, setSelectedUpload] = useState("");
  const [selectedFilename, setSelectedFilename] = useState("");
  const [sourceKind, setSourceKind] = useState("package");
  const [typeFilter, setTypeFilter] = useState("all");

  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedFilename(file.name);
    if (!projectId) { setNotice({ kind: "error", text: "请先输入新的 Project ID，再上传 Source Package。项目会在扫描时创建。" }); event.target.value = ""; return; }
    setLoading(true);
    try {
      const data = new FormData();
      data.append("file", file);
      data.append("source_kind", "package");
      const result = await request(pathFor(projectId, "/uploads"), { method: "POST", body: data });
      setSelectedUpload(result.source_ref);
      onUploaded?.(result);
      setNotice({ kind: "success", text: `Source 已存储为 ${result.source_ref}。` });
      // A first-time upload deliberately has no Project Intelligence yet; the
      // following scan creates it.  Do not turn that normal state into an
      // upload failure by attempting a refresh here.
    } catch (error) { setNotice({ kind: "error", text: error.message }); }
    finally { setLoading(false); event.target.value = ""; }
  };

  const scan = async () => {
    if (!selectedUpload || !projectId) { setNotice({ kind: "error", text: "请先上传 Package 并选择对应的 source reference。" }); return; }
    setLoading(true);
    try {
      const result = await request(pathFor(projectId, "/scan"), {
        method: "POST",
        body: JSON.stringify({ source_kind: sourceKind, source_ref: selectedUpload, version: version.trim() }),
      });
      if (result.intelligence) {
        await refreshProject(projectId);
      }
      const unresolved = result.scan.unresolved_reasons?.join(" ");
      setNotice({
        kind: result.scan.status === "ready" ? "success" : "error",
        text: result.scan.status === "ready" ? "Scan: ready" : `Scan: ${result.scan.status}${unresolved ? ` — ${unresolved}` : ""}`,
      });
    } catch (error) { setNotice({ kind: "error", text: error.message }); }
    finally { setLoading(false); }
  };

  if (!intelligence) {
    return (
      <Page
        title="Connect an Agent Project"
        kicker="PROJECT INTAKE"
        intro="输入一个新的 Project ID 后即可上传 source package。扫描会创建首个 Project Intelligence；不需要预先登记 skills 或 skill pairs。"
      >
        <section className="section-block">
          <SectionHeading label="First import" title="上传并扫描新的 Agent 项目" />
          <div className="form-layout">
            <div className="form-panel">
              <label className="field">Project ID
                <input
                  value={projectId}
                  onChange={(event) => onProjectIdChange?.(event.target.value)}
                  placeholder="例如 my-agent-project"
                  autoComplete="off"
                />
              </label>
              <div className="field">
                <span>Source Package</span>
                <div className="button-row" style={{ marginTop: 0 }}>
                  <FileButton id="project-source-upload-first" accept=".zip,.tar,.gz,application/zip" onChange={upload}>选择 Source Package</FileButton>
                  {selectedFilename && <span className="form-hint">已选择：{selectedFilename}</span>}
                </div>
                <p className="form-hint">支持 zip 或 tar package。上传成功后选择其 server-owned source reference 并执行扫描。</p>
              </div>
              <label className="field">已存储的 Source
                <select value={selectedUpload} onChange={(event) => setSelectedUpload(event.target.value)}>
                  <option value="">选择一个上传记录</option>
                  {uploads.map((item) => <option key={item.upload_id} value={item.source_ref}>{item.original_filename} / {item.source_ref}</option>)}
                </select>
              </label>
            </div>
            <div className="form-panel">
              <label className="field">Initial version
                <input value={version} onChange={(event) => setVersion(event.target.value)} />
              </label>
              <div><button className="primary" onClick={scan}><I name="radar" />扫描并创建项目</button></div>
              <p className="form-hint">Project ID 由顶部输入框提供。扫描失败会显示可操作的错误信息，不会创建伪造 discovery 结果。</p>
            </div>
          </div>
        </section>
      </Page>
    );
  }

  const { agent_manifest: manifest, baseline_snapshot: baseline, runtime_profile: runtime } = intelligence;
  const displayName = projectDisplayName(manifest, projectId);
  const registry = intelligence.capability_registry || [];
  const diffChanges = intelligence.latest_diff?.component_changes || [];
  const recommendations = diffChanges.filter((item) => DIFF_META[item.status]);
  const filteredRegistry = typeFilter === "all" ? registry : registry.filter((item) => item.component_type === typeFilter);
  const diffStatusFor = (name) => diffChanges.find((item) => item.component_name === name)?.status;
  const latestVersion = intelligence.latest_snapshot?.version || baseline.baseline_version;
  const skillCount = registry.filter((item) => item.component_type === "skill").length;
  const pairCount = registry.filter((item) => item.component_type === "skill_pair").length;
  const toolCount = registry.filter((item) => item.component_type === "tool").length;

  return (
    <Page
      title={displayName}
      kicker="项目总览 · PROJECT OVERVIEW"
      intro={manifest.purpose}
      action={<button className="primary" onClick={() => onNew(null)}><I name="flask" />新建评估</button>}
    >
      <div className="overview-grid">
        {/* Agent identity hero */}
        <section className="agent-hero">
          <div className="agent-tile"><I name="cube" /></div>
          <div className="agent-hero-info">
            <h2>{displayName}</h2>
            <p>{manifest.purpose}</p>
            <div className="chip-row">
              <span className="chip"><I name="database" />{manifest.source_kind}<span className="mono">{manifest.source_ref}</span></span>
              <span className="chip"><I name="branch" />Baseline <span className="mono">{baseline.baseline_version}</span></span>
              <span className="version-arrow">→</span>
              <span className="chip"><I name="sparkle" />Latest <span className="mono">{latestVersion}</span></span>
              <span className="chip"><I name="gear" />{runtime.runtime_kind}</span>
            </div>
          </div>
          <div className="agent-hero-side">
            <Status value={intelligence.status} />
            <div className="stat-row">
              <div className="stat-cell"><span className="eyebrow">Skills</span><strong>{skillCount}</strong></div>
              <div className="stat-cell"><span className="eyebrow">Pairs</span><strong>{pairCount}</strong></div>
              <div className="stat-cell"><span className="eyebrow">Tools</span><strong>{toolCount}</strong></div>
            </div>
            {onDetail && <button className="secondary" onClick={onDetail}><I name="cube" />项目详情<I name="arrowRight" /></button>}
          </div>
        </section>

        {/* Change recommendations from latest diff */}
        {recommendations.length > 0 && (
          <section className="section-block wide">
            <SectionHeading label="Change Detection" title="检测到组件变化 · 建议评估" />
            <div className="reco-grid">
              {recommendations.map((item) => {
                const meta = DIFF_META[item.status];
                return (
                  <button
                    className="diff-card"
                    key={`${item.component_type}:${item.component_name}`}
                    onClick={() => onNew({ componentType: item.component_type, componentName: item.component_name, changeType: meta.changeType })}
                  >
                    <div className="diff-head">
                      <CapIcon type={item.component_type} />
                      <strong>{item.component_name}</strong>
                      <span className="row-tail"><Status value={meta.label} /></span>
                    </div>
                    <p>{meta.hint}</p>
                    <span className="diff-cta">带入 New Evaluation<I name="arrowRight" /></span>
                  </button>
                );
              })}
            </div>
          </section>
        )}

        {/* Capability registry */}
        <section className="section-block wide">
          <SectionHeading label="Capability Registry" title={`能力组件注册表 · ${registry.length} 个组件`} />
          <div className="filter-row">
            {TYPE_FILTERS.map(([id, label]) => (
              <button key={id} className={typeFilter === id ? "filter-chip active" : "filter-chip"} onClick={() => setTypeFilter(id)}>{label}</button>
            ))}
          </div>
          <div className="capability-list">
            {filteredRegistry.map((item) => {
              const diffStatus = diffStatusFor(item.name);
              return (
                <div className="capability-row" key={item.capability_id}>
                  <CapIcon type={item.component_type} />
                  <div>
                    <span className="component-type">{item.component_type}</span>
                    <strong>{item.name}</strong>
                    <span className="muted">{item.responsibility}</span>
                  </div>
                  {diffStatus && DIFF_META[diffStatus] && <span className="row-tail"><Status value={DIFF_META[diffStatus].label} /></span>}
                </div>
              );
            })}
            {!filteredRegistry.length && <p className="muted">该类型下暂无注册组件。</p>}
          </div>
        </section>

        {/* Imported benchmark evidence */}
        <section className="section-block wide">
          <SectionHeading label="Benchmark Evidence" title={`已添加 Benchmark 结果 · ${benchmarks.length} 条`} />
          {benchmarks.length ? (
            <div className="benchmark-grid">
              {benchmarks.map((item) => (
                <article className="benchmark-card" key={item.evidence_id}>
                  <div className="diff-head"><I name="beaker" /><strong>{item.benchmark_name}</strong><Status value="external" /></div>
                  <div className="benchmark-metrics">
                    {(item.metrics || []).map((metric) => <span className="chip" key={`${item.evidence_id}:${metric.metric_name}`}>{metric.metric_name}: {metric.baseline_value} → {metric.candidate_value} {metric.unit}</span>)}
                  </div>
                  <p className="form-hint">外部 Benchmark 结果，仅作辅助证据；AIG 未执行该 Benchmark。</p>
                </article>
              ))}
            </div>
          ) : (
            <p className="muted">尚未添加 Benchmark 结果。可进入项目详情导入 JSON。</p>
          )}
        </section>

        {/* Runtime profile */}
        <section className="section-block">
          <SectionHeading label="Runtime Profile" title="运行时档案" />
          <dl className="definition-list">
            <div><dt>Entry point</dt><dd className="mono">{runtime.entrypoint}</dd></div>
            <div><dt>Runtime kind</dt><dd>{runtime.runtime_kind}</dd></div>
            <div><dt>Fixtures</dt><dd>{runtime.fixture_catalog.fixtures.length} 个已声明</dd></div>
          </dl>
        </section>

        {/* Scan history */}
        <section className="section-block">
          <SectionHeading label="Scan History" title={`扫描历史 · ${scans.length} 次`} />
          {scans.length ? (
            <div className="timeline">
              {scans.map((item) => {
                const tone = statusTone(item.status);
                return (
                  <div className={`timeline-item t-${tone === "mute" ? "info" : tone}`} key={item.scan_id}>
                    <div className="tl-head"><Status value={item.status} /><strong>{item.version}</strong></div>
                    <div className="tl-sub mono">{item.source_ref} · {item.findings?.length || 0} findings</div>
                  </div>
                );
              })}
            </div>
          ) : <p className="muted">暂无扫描记录。</p>}
        </section>

        {/* Project intake */}
        <section className="section-block wide">
          <SectionHeading label="Project Intake" title="上传并扫描候选版本" />
          <div className="form-layout">
            <div className="form-panel">
              <div className="field">
                <span>Source Package</span>
                <div className="button-row" style={{ marginTop: 0 }}>
                  <FileButton id="project-source-upload" accept=".zip,.tar,.gz,application/zip" onChange={upload}>选择 Source Package</FileButton>
                  {selectedFilename && <span className="form-hint">已选择：{selectedFilename}</span>}
                </div>
                <p className="form-hint">点击后会打开系统文件选择器；请选择 .zip / .tar.gz 包，选中后页面会显示文件名。</p>
              </div>
              <label className="field">已存储的 Source
                <select value={selectedUpload} onChange={(event) => setSelectedUpload(event.target.value)}>
                  <option value="">选择一个上传记录</option>
                  {uploads.map((item) => <option key={item.upload_id} value={item.source_ref}>{item.original_filename} / {item.source_ref}</option>)}
                </select>
              </label>
            </div>
            <div className="form-panel">
              <label className="field">候选版本号
                <input value={version} onChange={(event) => setVersion(event.target.value)} />
              </label>
              <label className="field">Source kind
                <select value={sourceKind} onChange={(event) => setSourceKind(event.target.value)}>
                  <option value="package">package</option>
                </select>
              </label>
              <div><button className="secondary" onClick={scan}><I name="radar" />扫描所选 Source</button></div>
            </div>
          </div>
          <p className="form-hint">浏览器仅负责发送文件；source reference、fingerprint 与扫描结果均由服务端拥有。</p>
        </section>

        {/* Workflow strip */}
        <div className="workflow-strip">
          {WORKFLOW.map(([icon, en, zh]) => (
            <div className="workflow-node" key={en}>
              <span className="wf-icon"><I name={icon} /></span>
              <strong>{en}</strong>
              <small>{zh}</small>
            </div>
          ))}
        </div>
      </div>
    </Page>
  );
}
