import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { API_BASE, stored, save, projectStorageKey, pathFor, getError, collection, projectDisplayName, projectPurpose } from "./lib.js";
import { I } from "./components.jsx";
import Overview from "./pages/Overview.jsx";
import ProjectDetail from "./pages/ProjectDetail.jsx";
import NewEvaluation from "./pages/NewEvaluation.jsx";
import Running from "./pages/Running.jsx";
import Report from "./pages/Report.jsx";
import Copilot from "./Copilot.jsx";

const NAV_ITEMS = [
  ["overview", "总览", "Overview", "overview"],
  ["project", "项目详情", "Project Detail", "cube"],
  ["new", "新建评估", "New Evaluation", "flask"],
  ["running", "运行监控", "Running", "pulse"],
  ["report", "评估报告", "Report", "doc"],
];

const LIGHTTABLE_ID = "lighttable-pair-nutrition";
const requestedDemo = new URLSearchParams(window.location.search).get("demo") === "lighttable";
const configuredDemo = String(import.meta.env.VITE_AIG_DEMO_MODE || "").toLowerCase() === "true";
const initialProjectId = configuredDemo || requestedDemo ? LIGHTTABLE_ID : (localStorage.getItem("aig.projectId") || "");
const DEMO_NOTICE = "LightTable 示例项目已加载，仅供浏览，编辑操作已锁定。";
const READ_ONLY_NOTICE = "此项目仅供示意，不可编辑。请上传正式 Project 后执行此操作。";
const initialDemoMode = configuredDemo || requestedDemo || localStorage.getItem("aig.demoMode") === "1";
const captureMode = new URLSearchParams(window.location.search).get("capture") === "1";

function App() {
  const [activeView, setActiveView] = useState("overview");
  const [projectId, setProjectId] = useState(initialProjectId);
  const [intelligence, setIntelligence] = useState(null);
  const [scans, setScans] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [providers, setProviders] = useState([]);
  const [executionConfigs, setExecutionConfigs] = useState([]);
  const [knowledge, setKnowledge] = useState([]);
  const [benchmarks, setBenchmarks] = useState([]);
  const [reportList, setReportList] = useState([]);
  const [requestRecord, setRequestRecord] = useState(() => stored(projectStorageKey(initialProjectId, "request")));
  const [plan, setPlan] = useState(() => stored(projectStorageKey(initialProjectId, "plan")));
  const [readiness, setReadiness] = useState(() => stored(projectStorageKey(initialProjectId, "readiness")));
  const [run, setRun] = useState(() => stored(projectStorageKey(initialProjectId, "run")));
  const [matrix, setMatrix] = useState(() => stored(projectStorageKey(initialProjectId, "matrix")));
  const [evidence, setEvidence] = useState(() => stored(projectStorageKey(initialProjectId, "evidence")));
  const [reportEvidence, setReportEvidence] = useState(() => stored(projectStorageKey(initialProjectId, "reportEvidence")));
  const [report, setReport] = useState(() => stored(projectStorageKey(initialProjectId, "report")));
  const [reportView, setReportView] = useState(() => stored(projectStorageKey(initialProjectId, "reportView")));
  const [gate, setGate] = useState(() => stored(projectStorageKey(initialProjectId, "gate")));
  const [fixtureRoot, setFixtureRoot] = useState(() => localStorage.getItem(projectStorageKey(initialProjectId, "fixtureRoot")) || "");
  const [runContext, setRunContext] = useState(() => stored(projectStorageKey(initialProjectId, "runContext")));
  const [demoMode, setDemoMode] = useState(initialDemoMode);
  const [evalPrefill, setEvalPrefill] = useState(null);
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(false);

  const projectHeader = intelligence ? {
    displayName: projectDisplayName(intelligence.agent_manifest || {}, projectId),
    purpose: projectPurpose(intelligence.agent_manifest || {}, projectId),
    baselineVersion: intelligence.baseline_snapshot?.baseline_version || "",
    candidateVersion: intelligence.latest_snapshot?.version || intelligence.baseline_snapshot?.version || "",
    runtimeKind: intelligence.runtime_profile?.runtime_kind || "",
    status: intelligence.status || "pending",
  } : null;

  const request = async (path, options = {}) => {
    const isForm = options.body instanceof FormData;
    const headers = isForm ? { ...(options.headers || {}) } : { "content-type": "application/json", ...(options.headers || {}) };
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getError(body, response.status));
    return body;
  };

  const restoreProjectArtifacts = (id) => {
    const read = (key) => stored(projectStorageKey(id, key));
    setRequestRecord(read("request"));
    setPlan(read("plan"));
    setReadiness(read("readiness"));
    setRun(read("run"));
    setMatrix(read("matrix"));
    setEvidence(read("evidence"));
    setReportEvidence(read("reportEvidence"));
    setReport(read("report"));
    setReportView(read("reportView"));
    setGate(read("gate"));
    setRunContext(read("runContext"));
    setFixtureRoot(localStorage.getItem(projectStorageKey(id, "fixtureRoot")) || "");
    setEvalPrefill(null);
  };

  const exitProject = () => {
    setProjectId("");
    setDemoMode(false);
    setIntelligence(null);
    setScans([]);
    setUploads([]);
    setProviders([]);
    setExecutionConfigs([]);
    setKnowledge([]);
    setBenchmarks([]);
    setReportList([]);
    setRequestRecord(null);
    setPlan(null);
    setReadiness(null);
    setRun(null);
    setMatrix(null);
    setEvidence(null);
    setReportEvidence(null);
    setReport(null);
    setReportView(null);
    setGate(null);
    setFixtureRoot("");
    setRunContext(null);
    setEvalPrefill(null);
    setActiveView("overview");
    localStorage.removeItem("aig.projectId");
    localStorage.removeItem("aig.demoMode");
    setNotice({ kind: "success", text: "已退出当前项目，请上传项目文件或选择 LightTable 示例。" });
  };

  const refreshProject = async (value = projectId) => {
    const id = value.trim();
    if (!id) throw new Error("请先输入项目 ID。");
    const [nextIntelligence, rawScans, rawUploads, rawProviders, rawConfigs, rawReports, rawKnowledge, rawBenchmarks] = await Promise.all([
      request(pathFor(id, "/intelligence")),
      request(pathFor(id, "/scans")),
      request(pathFor(id, "/uploads")),
      request(pathFor(id, "/provider-bindings")),
      request(pathFor(id, "/evaluation-execution-configurations")),
      request(pathFor(id, "/reports")),
      request(pathFor(id, "/evaluation-knowledge")),
      request(pathFor(id, "/benchmark-evidence")),
    ]);
    const nextScans = collection(rawScans);
    const nextUploads = collection(rawUploads);
    const nextProviders = collection(rawProviders);
    const nextConfigs = collection(rawConfigs);
    const nextReports = collection(rawReports);
    const nextKnowledge = collection(rawKnowledge);
    const nextBenchmarks = collection(rawBenchmarks);
    setProjectId(id);
    localStorage.setItem("aig.projectId", id);
    setIntelligence(nextIntelligence);
    setScans(nextScans);
    setUploads(nextUploads);
    setProviders(nextProviders);
    setExecutionConfigs(nextConfigs);
    setReportList(nextReports);
    setKnowledge(nextKnowledge);
    setBenchmarks(nextBenchmarks);
    restoreProjectArtifacts(id);
    const persistedReport = stored(projectStorageKey(id, "report"));
    if (persistedReport?.report_id) {
      try {
        const persistedView = await request(pathFor(id, `/reports/${encodeURIComponent(persistedReport.report_id)}/view`));
        setReportView(persistedView);
        save(projectStorageKey(id, "reportView"), persistedView);
      } catch {
        setReportView(null);
        save(projectStorageKey(id, "reportView"), null);
      }
    } else {
      setReportView(null);
    }
    return nextIntelligence;
  };

  const importBenchmark = async (file) => {
    if (demoMode) { setNotice({ kind: "error", text: READ_ONLY_NOTICE }); return; }
    if (!file || !projectId) {
      setNotice({ kind: "error", text: "请先加载项目，再导入 Benchmark JSON。" });
      return;
    }
    setLoading(true);
    try {
      const raw = JSON.parse(await file.text());
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("Benchmark JSON 必须是一个对象。 ");
      const result = await request(pathFor(projectId, "/benchmark-evidence"), {
        method: "POST",
        body: JSON.stringify({ result: raw, source_ref: `browser:${file.name}` }),
      });
      setBenchmarks((current) => [result, ...current.filter((item) => item.evidence_id !== result.evidence_id)]);
      setNotice({ kind: "success", text: `Benchmark ${result.benchmark_name} 已导入。` });
      await refreshProject(projectId);
    } catch (error) {
      setNotice({ kind: "error", text: `Benchmark 导入失败：${error.message}` });
    } finally {
      setLoading(false);
    }
  };

  const loadProject = async () => {
    setLoading(true);
    setNotice(null);
    if (!projectId.trim()) {
      exitProject();
      setLoading(false);
      return;
    }
    try {
      setDemoMode(false);
      localStorage.removeItem("aig.demoMode");
      await refreshProject();
      setNotice({ kind: "success", text: "项目、扫描记录、Provider、执行契约与报告已加载。" });
    } catch (error) {
      setIntelligence(null);
      setNotice({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  };

  const loadDemoProject = async () => {
    setLoading(true);
    setNotice(null);
    try {
      const [rawIntelligence, rawScans, rawUploads, rawProviders, rawConfigs, rawKnowledge, rawBenchmarks, demoBundle] = await Promise.all([
        Promise.resolve(null),
        Promise.resolve([]),
        Promise.resolve([]),
        Promise.resolve([]),
        Promise.resolve([]),
        Promise.resolve([]),
        Promise.resolve([]),
        request("/api/v1/demo/reports/lighttable"),
      ]);
      const demoReport = demoBundle.report;
      const demoEvidence = demoBundle.evidence;
      const demoGate = demoBundle.gate;
      const nextIntelligence = rawIntelligence || {
        schema_version: "aig.project-intelligence.v1",
        project_id: LIGHTTABLE_ID,
        status: "ready",
        agent_manifest: { project_id: LIGHTTABLE_ID, agent_name: "LightTable", purpose: "当前已加载 LightTable 项目，可查看版本、能力变化与评估结果。", source_kind: "repository", source_ref: "demo://lighttable", available_components: ["recipe_planning"], capability_descriptions: { recipe_planning: demoReport.product_overview?.product_role || "生成符合约束的餐食计划。" } },
        capability_registry: [{ project_id: LIGHTTABLE_ID, component_type: "skill", name: "recipe_planning", responsibility: demoReport.product_overview?.product_role || "生成符合约束的餐食计划。", boundary: [demoReport.product_overview?.boundary || "仅覆盖示例中的餐食规划任务。"], status: "observed" }],
        runtime_profile: { project_id: LIGHTTABLE_ID, runtime_kind: "native_command", entrypoint: "demo://lighttable", execution_requirements: ["只读示例"], source_ref: "demo://lighttable" },
        baseline_snapshot: { project_id: LIGHTTABLE_ID, baseline_version: "main-fa774ef", snapshot_id: "demo-baseline", runtime_profile: { runtime_kind: "native_command" } },
        latest_snapshot: { project_id: LIGHTTABLE_ID, version: "candidate-gui-v2-20260806", snapshot_id: "demo-candidate", runtime_profile: { runtime_kind: "native_command" } },
        snapshot_history: [],
        latest_diff: { component_changes: [{ component_type: "skill", component_name: "recipe_planning", status: "changed", responsibility: demoReport.product_overview?.product_role || "生成符合约束的餐食计划。" }] },
      };
      const nextReports = [{ report_id: demoReport.report_id, project_id: LIGHTTABLE_ID, source: "demo", run_id: "demo-run-lighttable", created_at: demoReport.generated_at || "示例报告" }];
      setDemoMode(true);
      setProjectId(LIGHTTABLE_ID);
      localStorage.setItem("aig.projectId", LIGHTTABLE_ID);
      localStorage.setItem("aig.demoMode", "1");
      setIntelligence(nextIntelligence);
      setScans(collection(rawScans)); setUploads(collection(rawUploads)); setProviders(collection(rawProviders));
      setExecutionConfigs(collection(rawConfigs)); setReportList(nextReports); setKnowledge(collection(rawKnowledge));
      setBenchmarks(collection(rawBenchmarks));
      setRequestRecord(null); setPlan(null); setReadiness(null); setRun(null); setRunContext(null);
      setFixtureRoot("");
      const demoPlan = demoReport.evaluation_plan;
      const demoRun = { run_id: "demo-run-lighttable", status: "completed", current_stage: "completed", progress_percent: 100, events: [{ event_id: "demo-start", stage: "planning", status: "completed", detail: "示例评估计划已冻结" }, { event_id: "demo-finish", stage: "report", status: "completed", detail: "示例 Evidence 已生成" }], artifact: { conditions: demoEvidence.conditions || [], metrics: demoEvidence.summary || {} } };
      setMatrix(demoEvidence); setEvidence(demoEvidence); setReportEvidence(demoEvidence); setReport(demoReport); setReportView(demoBundle.view); setGate(demoGate); setPlan(demoPlan); setReadiness({ status: "ready", blocking_reasons: [] }); setRun(demoRun); setRequestRecord({ request_id: "demo-request-lighttable" });
      save(projectStorageKey(LIGHTTABLE_ID, "report"), demoReport); save(projectStorageKey(LIGHTTABLE_ID, "reportView"), demoBundle.view); save(projectStorageKey(LIGHTTABLE_ID, "reportEvidence"), demoEvidence); save(projectStorageKey(LIGHTTABLE_ID, "gate"), demoGate); save(projectStorageKey(LIGHTTABLE_ID, "plan"), demoPlan); save(projectStorageKey(LIGHTTABLE_ID, "run"), demoRun);
      setRunContext({ executionConfigId: collection(rawConfigs)[0]?.config_id || "demo-config", providerBindingId: collection(rawProviders).find((item) => item.role === "control_plane")?.provider_binding_id || "demo-provider", productDefinition: demoReport.product_context || {} });
      setActiveView("overview");
      setNotice({ kind: "success", text: DEMO_NOTICE });
    } catch (error) {
      setIntelligence(null);
      setNotice({ kind: "error", text: `LightTable 示例项目加载失败：${error.message}` });
    } finally { setLoading(false); }
  };

  const loadReadiness = async () => {
    if (!plan || !projectId) return;
    setLoading(true);
    try {
      const value = await request(pathFor(projectId, "/evaluations/readiness"), {
        method: "POST",
        body: JSON.stringify({ evaluation_plan: plan, fixture_root: fixtureRoot || undefined, execution_config_id: runContext?.executionConfigId || undefined }),
      });
      setReadiness(value);
      save(projectStorageKey(projectId, "readiness"), value);
      setNotice({ kind: value.status === "ready" ? "success" : "error", text: `Readiness: ${value.status}` });
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  };

  const loadRun = async () => {
    if (!run || !projectId) return;
    setLoading(true);
    try {
      const value = await request(pathFor(projectId, `/evaluations/runs/${encodeURIComponent(run.run_id)}`));
      setRun(value);
      save(projectStorageKey(projectId, "run"), value);
      setNotice({ kind: "success", text: `Run status: ${value.status} / ${value.current_stage}` });
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  };

  // Silent status poll for the live Running view: updates state without
  // toggling the global spinner or emitting a notice each tick.
  const pollRun = async () => {
    if (!run || !projectId) return;
    try {
      const value = await request(pathFor(projectId, `/evaluations/runs/${encodeURIComponent(run.run_id)}`));
      setRun(value);
      save(projectStorageKey(projectId, "run"), value);
    } catch {
      // Keep the last known state on transient poll failures.
    }
  };

  const loadRunArtifacts = async () => {
    if (!run || !requestRecord || !projectId) return;
    setLoading(true);
    try {
      const root = `${pathFor(projectId, `/evaluations/${encodeURIComponent(requestRecord.request_id)}/runs/${encodeURIComponent(run.run_id)}`)}`;
      const [nextMatrix, nextEvidence] = await Promise.all([
        request(`${root}/matrix`),
        request(`${root}/evidence`),
      ]);
      setMatrix(nextMatrix);
      setEvidence(nextEvidence);
      save(projectStorageKey(projectId, "matrix"), nextMatrix);
      save(projectStorageKey(projectId, "evidence"), nextEvidence);
      setNotice({ kind: "success", text: "Matrix 与 Evidence Bundle 已从服务端加载。" });
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  };

  const openReport = async (reportId, reportRunId) => {
    setLoading(true);
    try {
      let value;
      let nextEvidence;
      let nextGate;
      let nextView;
      if (demoMode) {
        const bundle = await request("/api/v1/demo/reports/lighttable");
        value = bundle.report;
        nextEvidence = bundle.evidence;
        nextGate = bundle.gate;
        nextView = bundle.view;
      } else {
        [value, nextEvidence, nextView] = await Promise.all([
          request(pathFor(projectId, `/reports/${encodeURIComponent(reportId)}`)),
          request(pathFor(projectId, `/reports/${encodeURIComponent(reportId)}/evidence`)),
          request(pathFor(projectId, `/reports/${encodeURIComponent(reportId)}/view`)),
        ]);
        nextGate = await request(pathFor(projectId, "/release-decision"), { method: "POST", body: JSON.stringify({ report: value }) });
      }
      setReport(value);
      setReportView(nextView);
      setGate(nextGate);
      setReportEvidence(nextEvidence);
      save(projectStorageKey(projectId, "report"), value);
      save(projectStorageKey(projectId, "reportView"), nextView);
      save(projectStorageKey(projectId, "gate"), nextGate);
      save(projectStorageKey(projectId, "reportEvidence"), nextEvidence);
      if (reportRunId) setRun((current) => current || { run_id: reportRunId });
      setActiveView("report");
      setNotice({ kind: "success", text: `报告 ${reportId} 已加载${demoMode ? "（示例项目只读）" : "，Gate 已评估"}。` });
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  };

  const importReport = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (demoMode) { setNotice({ kind: "error", text: READ_ONLY_NOTICE }); event.target.value = ""; return; }
    try {
      const raw = JSON.parse(await file.text());
      const value = raw.report || raw.data?.report || raw;
      const importedProjectId = value.subject?.product_id;
      if (!importedProjectId) throw new Error("Report subject.product_id is required.");
      const result = await request(pathFor(importedProjectId, "/reports"), {
        method: "POST",
        body: JSON.stringify({ report: value }),
      });
      const importedView = await request(pathFor(importedProjectId, `/reports/${encodeURIComponent(result.report.report_id)}/view`));
      setProjectId(importedProjectId);
      localStorage.setItem("aig.projectId", importedProjectId);
      setReport(result.report);
      setReportView(importedView);
      setGate(result.gate);
      setReportEvidence(null);
      save(projectStorageKey(importedProjectId, "report"), result.report);
      save(projectStorageKey(importedProjectId, "reportView"), importedView);
      save(projectStorageKey(importedProjectId, "gate"), result.gate);
      save(projectStorageKey(importedProjectId, "reportEvidence"), null);
      setNotice({ kind: result.gate.decision === "block" ? "error" : "success", text: `报告已导入；Gate: ${result.gate.decision}。` });
      setActiveView("report");
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      event.target.value = "";
    }
  };

  useEffect(() => {
    if (!projectId) return;
    if (initialDemoMode && projectId === LIGHTTABLE_ID) loadDemoProject();
    else loadProject();
    // The persisted project is loaded once when the application opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openNewEvaluation = (prefill) => {
    if (demoMode) { setNotice({ kind: "error", text: READ_ONLY_NOTICE }); return; }
    setEvalPrefill(prefill || null); setActiveView("new");
  };

  return (
    <div className={`app-shell${captureMode ? " capture-mode" : ""}`}>
      <aside className="rail">
        <div className="brand-lockup">
          <span className="brand-mark"><img src="/icons/agent-guard-logo.svg" alt="Agent Iteration Guard" /></span>
          <span className="brand-text">
            <strong>Agent Iteration Guard</strong>
            <span>AIG · v1.0</span>
          </span>
        </div>
        <div className="rail-caption">项目工作台</div>
        <nav className="nav-list" aria-label="Primary">
          {NAV_ITEMS.map(([id, zh, en, icon]) => (
            <button key={id} className={activeView === id ? "nav-item active" : "nav-item"} onClick={() => setActiveView(id)}>
              <I name={icon} />
              <span className="nav-zh">{zh}</span>
              <span className="nav-en">{en}</span>
            </button>
          ))}
        </nav>
        <div className="rail-footer"><span>Local workspace</span><strong>Evidence-first</strong></div>
      </aside>

      <main className="main-column">
        <header className="topbar">
          <div className="topbar-left">
            <span className="topbar-title">Agent 能力演进评估工作台</span>
            {demoMode && <span className="demo-badge">LightTable Demo · 只读</span>}
          </div>
          <div className="project-switcher">
            <span>项目</span>
            <input
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && loadProject()}
              placeholder="输入 Project ID"
            />
            <button onClick={loadProject} disabled={loading}>{loading ? "加载中…" : "加载项目"}</button>
          </div>
        </header>

        {demoMode && intelligence && (
          <div className="notice-wrap">
            <div className="notice success" role="status" aria-live="polite">
              <I name="check" />
              {DEMO_NOTICE}
            </div>
          </div>
        )}

        {notice && (!demoMode || notice.kind === "error") && notice.text !== DEMO_NOTICE && (
          <div className="notice-wrap">
            <div className={`notice ${notice.kind}`}>
              <I name={notice.kind === "error" ? "alert" : "check"} />
              {notice.text}
            </div>
          </div>
        )}

        <div key={activeView}>
          {activeView === "overview" && <Overview
            projectId={projectId}
            intelligence={intelligence}
            scans={scans}
            uploads={uploads}
            benchmarks={benchmarks}
            reportList={reportList}
            report={report}
            reportView={reportView}
            gate={gate}
            onNew={openNewEvaluation}
            onDetail={() => setActiveView("project")}
            onExitProject={exitProject}
            onReport={openReport}
            onUploaded={(upload) => setUploads((current) => [upload, ...current.filter((item) => item.upload_id !== upload.upload_id)])}
            onProjectIdChange={setProjectId}
            request={request}
            refreshProject={refreshProject}
            setLoading={setLoading}
            setNotice={setNotice}
            demoMode={demoMode}
            onDemoLoad={loadDemoProject}
            readOnlyNotice={() => setNotice({ kind: "error", text: READ_ONLY_NOTICE })}
          />}
          {activeView === "project" && <ProjectDetail
            projectId={projectId}
            intelligence={intelligence}
            projectHeader={projectHeader}
            knowledge={knowledge}
            reportList={reportList}
            providers={providers}
            executionConfigs={executionConfigs}
            benchmarks={benchmarks}
            onImportBenchmark={importBenchmark}
            onNew={openNewEvaluation}
            onReport={openReport}
            onOverview={() => setActiveView("overview")}
            demoMode={demoMode}
            readOnlyNotice={() => setNotice({ kind: "error", text: READ_ONLY_NOTICE })}
          />}
          {activeView === "new" && <NewEvaluation
            key={projectId}
            projectId={projectId}
            intelligence={intelligence}
            projectHeader={projectHeader}
            providers={providers}
            executionConfigs={executionConfigs}
            knowledge={knowledge}
            fixtureRoot={fixtureRoot}
            setFixtureRoot={(value) => { setFixtureRoot(value); localStorage.setItem(projectStorageKey(projectId, "fixtureRoot"), value); }}
            request={request}
            onOverview={() => setActiveView("overview")}
            setLoading={setLoading}
            setNotice={setNotice}
            onConfigurationChanged={() => refreshProject(projectId)}
            prefill={evalPrefill}
            onPlan={(value, requestValue, context) => {
              setPlan(value); setRequestRecord(requestValue); setRunContext(context);
              save(projectStorageKey(projectId, "plan"), value); save(projectStorageKey(projectId, "request"), requestValue); save(projectStorageKey(projectId, "runContext"), context);
              setReadiness(null); setRun(null); setMatrix(null); setEvidence(null);
              setReportEvidence(null);
              setReport(null); setReportView(null); setGate(null);
              save(projectStorageKey(projectId, "readiness"), null); save(projectStorageKey(projectId, "run"), null); save(projectStorageKey(projectId, "matrix"), null); save(projectStorageKey(projectId, "evidence"), null);
              save(projectStorageKey(projectId, "reportEvidence"), null);
              save(projectStorageKey(projectId, "report"), null); save(projectStorageKey(projectId, "reportView"), null); save(projectStorageKey(projectId, "gate"), null);
              setActiveView("running");
            }}
            demoMode={demoMode}
            readOnlyNotice={() => setNotice({ kind: "error", text: READ_ONLY_NOTICE })}
          />}
          {activeView === "running" && <Running
            projectId={projectId}
            projectHeader={projectHeader}
            plan={plan}
            requestRecord={requestRecord}
            runContext={runContext}
            readiness={readiness}
            run={run}
            matrix={matrix}
            evidence={evidence}
            onReadiness={loadReadiness}
            onStart={async () => {
              if (!readiness || readiness.status !== "ready") {
                setNotice({ kind: "error", text: "Readiness 必须就绪后才能启动评估。" });
                return;
              }
              if (!runContext?.executionConfigId) {
                setNotice({ kind: "error", text: "请先在 New Evaluation 中选择服务端拥有的执行配置。" });
                return;
              }
              setLoading(true);
              try {
                const value = await request(pathFor(projectId, `/evaluations/${encodeURIComponent(requestRecord.request_id)}/runs`), {
                  method: "POST",
                  body: JSON.stringify({ evaluation_plan_id: plan.plan_id, execution_config_id: runContext.executionConfigId }),
                });
                setRun(value); save(projectStorageKey(projectId, "run"), value);
                setNotice({ kind: value.status === "completed" ? "success" : "error", text: `Evaluation Run: ${value.status}。` });
              } catch (error) { setNotice({ kind: "error", text: error.message }); }
              finally { setLoading(false); }
            }}
            onRefresh={loadRun}
            onPoll={pollRun}
            onArtifacts={loadRunArtifacts}
            onReport={async () => {
              if (!run || run.status !== "completed" || !runContext?.providerBindingId) {
                setNotice({ kind: "error", text: "生成报告需要一个已完成的 Run 和 control-plane Provider Binding。" });
                return;
              }
              setLoading(true);
              try {
                const value = await request(pathFor(projectId, `/evaluations/runs/${encodeURIComponent(run.run_id)}/report`), {
                  method: "POST",
                  body: JSON.stringify({ run_id: run.run_id, provider_binding_id: runContext.providerBindingId, product_definition: runContext.productDefinition }),
                });
                setReport(value); save(projectStorageKey(projectId, "report"), value);
                const nextEvidence = await request(pathFor(projectId, `/reports/${encodeURIComponent(value.report_id)}/evidence`));
                setReportEvidence(nextEvidence); save(projectStorageKey(projectId, "reportEvidence"), nextEvidence);
                const nextView = await request(pathFor(projectId, `/reports/${encodeURIComponent(value.report_id)}/view`));
                setReportView(nextView); save(projectStorageKey(projectId, "reportView"), nextView);
                const nextGate = await request(pathFor(projectId, "/release-decision"), { method: "POST", body: JSON.stringify({ report: value }) });
                setGate(nextGate); save(projectStorageKey(projectId, "gate"), nextGate);
                await refreshProject(projectId);
                setActiveView("report");
                setNotice({ kind: "success", text: `Product Evaluation Report 已生成；Gate: ${nextGate.decision}。` });
              } catch (error) { setNotice({ kind: "error", text: error.message }); }
              finally { setLoading(false); }
            }}
            loading={loading}
            onOverview={() => setActiveView("overview")}
            demoMode={demoMode}
            readOnlyNotice={() => setNotice({ kind: "error", text: READ_ONLY_NOTICE })}
          />}
          {activeView === "report" && <Report
            projectId={projectId}
            projectHeader={projectHeader}
            report={report}
            reportView={reportView}
            reportList={reportList}
            onOpen={openReport}
            onImport={importReport}
            onRefresh={async () => { try { await refreshProject(projectId); setNotice({ kind: "success", text: "报告列表已刷新。" }); } catch (error) { setNotice({ kind: "error", text: error.message }); } }}
            loading={loading}
            onOverview={() => setActiveView("overview")}
            demoMode={demoMode}
            readOnlyNotice={() => setNotice({ kind: "error", text: READ_ONLY_NOTICE })}
          />}
        </div>
      </main>
      <Copilot
        projectId={projectId}
        intelligence={intelligence}
        providers={providers}
        activeView={activeView}
        request={request}
        onProjectRefresh={() => refreshProject(projectId)}
        onNavigate={(reference) => {
          if (reference.target_view === "report") {
            const metadata = reportList.find((item) => item.report_id === reference.object_id);
            if (metadata) openReport(metadata.report_id, metadata.run_id);
            else setActiveView("report");
            return;
          }
          setActiveView(reference.target_view);
        }}
      />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
