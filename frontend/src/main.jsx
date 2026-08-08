import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { API_BASE, stored, save, projectStorageKey, pathFor, getError } from "./lib.js";
import { I } from "./components.jsx";
import Overview from "./pages/Overview.jsx";
import ProjectDetail from "./pages/ProjectDetail.jsx";
import NewEvaluation from "./pages/NewEvaluation.jsx";
import Running from "./pages/Running.jsx";
import Report from "./pages/Report.jsx";

const NAV_ITEMS = [
  ["overview", "总览", "Overview", "overview"],
  ["project", "项目详情", "Project Detail", "cube"],
  ["new", "新建评估", "New Evaluation", "flask"],
  ["running", "运行监控", "Running", "pulse"],
  ["report", "评估报告", "Report", "doc"],
];

const initialProjectId = localStorage.getItem("aig.projectId") || "";

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
  const [gate, setGate] = useState(() => stored(projectStorageKey(initialProjectId, "gate")));
  const [fixtureRoot, setFixtureRoot] = useState(() => localStorage.getItem(projectStorageKey(initialProjectId, "fixtureRoot")) || "");
  const [runContext, setRunContext] = useState(() => stored(projectStorageKey(initialProjectId, "runContext")));
  const [evalPrefill, setEvalPrefill] = useState(null);
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(false);

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
    setGate(read("gate"));
    setRunContext(read("runContext"));
    setFixtureRoot(localStorage.getItem(projectStorageKey(id, "fixtureRoot")) || "");
    setEvalPrefill(null);
  };

  const refreshProject = async (value = projectId) => {
    const id = value.trim();
    if (!id) throw new Error("请先输入项目 ID。");
    const [nextIntelligence, nextScans, nextUploads, nextProviders, nextConfigs, nextReports, nextKnowledge, nextBenchmarks] = await Promise.all([
      request(pathFor(id, "/intelligence")),
      request(pathFor(id, "/scans")),
      request(pathFor(id, "/uploads")),
      request(pathFor(id, "/provider-bindings")),
      request(pathFor(id, "/evaluation-execution-configurations")),
      request(pathFor(id, "/reports")),
      request(pathFor(id, "/evaluation-knowledge")),
      request(pathFor(id, "/benchmark-evidence")),
    ]);
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
    return nextIntelligence;
  };

  const importBenchmark = async (file) => {
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
    try {
      await refreshProject();
      setNotice({ kind: "success", text: "项目、扫描记录、Provider、执行契约与报告已加载。" });
    } catch (error) {
      setIntelligence(null);
      setNotice({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
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
      const value = await request(pathFor(projectId, `/reports/${encodeURIComponent(reportId)}`));
      const [nextGate, nextEvidence] = await Promise.all([
        request(pathFor(projectId, "/release-decision"), {
          method: "POST",
          body: JSON.stringify({ report: value }),
        }),
        request(pathFor(projectId, `/reports/${encodeURIComponent(reportId)}/evidence`)),
      ]);
      setReport(value);
      setGate(nextGate);
      setReportEvidence(nextEvidence);
      save(projectStorageKey(projectId, "report"), value);
      save(projectStorageKey(projectId, "gate"), nextGate);
      save(projectStorageKey(projectId, "reportEvidence"), nextEvidence);
      if (reportRunId) setRun((current) => current || { run_id: reportRunId });
      setActiveView("report");
      setNotice({ kind: "success", text: `报告 ${reportId} 已加载，Gate 已评估。` });
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  };

  const importReport = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const raw = JSON.parse(await file.text());
      const value = raw.report || raw.data?.report || raw;
      const importedProjectId = value.subject?.product_id;
      if (!importedProjectId) throw new Error("Report subject.product_id is required.");
      const result = await request(pathFor(importedProjectId, "/reports"), {
        method: "POST",
        body: JSON.stringify({ report: value }),
      });
      setProjectId(importedProjectId);
      localStorage.setItem("aig.projectId", importedProjectId);
      setReport(result.report);
      setGate(result.gate);
      setReportEvidence(null);
      save(projectStorageKey(importedProjectId, "report"), result.report);
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
    loadProject();
    // The persisted project is loaded once when the application opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="brand-lockup">
          <span className="brand-mark"><I name="shield" /></span>
          <span className="brand-text">
            <strong>Agent Iteration Guard</strong>
            <span>AIG · v1.0</span>
          </span>
        </div>
        <div className="rail-caption">Capability Evolution · Local</div>
        <nav className="nav-list" aria-label="Primary">
          {NAV_ITEMS.map(([id, zh, en, icon]) => (
            <button key={id} className={activeView === id ? "nav-item active" : "nav-item"} onClick={() => setActiveView(id)}>
              <I name={icon} />
              <span className="nav-zh">{zh}</span>
              <span className="nav-en">{en}</span>
            </button>
          ))}
        </nav>
        <div className="rail-footer"><span className="live-dot" /><span>证据驱动 · Evidence-first</span></div>
      </aside>

      <main className="main-column">
        <header className="topbar">
          <div className="topbar-left">
            <span className="topbar-badge">AIG v1.0</span>
            <span className="topbar-title">Agent 能力演进评估工作台</span>
          </div>
          <div className="project-switcher">
            <span>Project</span>
            <input
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && loadProject()}
              placeholder="project-id"
            />
            <button onClick={loadProject} disabled={loading}>{loading ? "加载中…" : "加载"}</button>
          </div>
        </header>

        {notice && (
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
            onNew={(prefill) => { setEvalPrefill(prefill || null); setActiveView("new"); }}
            onDetail={() => setActiveView("project")}
            onUploaded={(upload) => setUploads((current) => [upload, ...current.filter((item) => item.upload_id !== upload.upload_id)])}
            onProjectIdChange={setProjectId}
            request={request}
            refreshProject={refreshProject}
            setLoading={setLoading}
            setNotice={setNotice}
          />}
          {activeView === "project" && <ProjectDetail
            projectId={projectId}
            intelligence={intelligence}
            knowledge={knowledge}
            reportList={reportList}
            providers={providers}
            executionConfigs={executionConfigs}
            benchmarks={benchmarks}
            onImportBenchmark={importBenchmark}
            onNew={(prefill) => { setEvalPrefill(prefill || null); setActiveView("new"); }}
            onReport={openReport}
            onOverview={() => setActiveView("overview")}
          />}
          {activeView === "new" && <NewEvaluation
            key={projectId}
            projectId={projectId}
            intelligence={intelligence}
            providers={providers}
            executionConfigs={executionConfigs}
            knowledge={knowledge}
            fixtureRoot={fixtureRoot}
            setFixtureRoot={(value) => { setFixtureRoot(value); localStorage.setItem(projectStorageKey(projectId, "fixtureRoot"), value); }}
            request={request}
            setLoading={setLoading}
            setNotice={setNotice}
            onConfigurationChanged={() => refreshProject(projectId)}
            prefill={evalPrefill}
            onPlan={(value, requestValue, context) => {
              setPlan(value); setRequestRecord(requestValue); setRunContext(context);
              save(projectStorageKey(projectId, "plan"), value); save(projectStorageKey(projectId, "request"), requestValue); save(projectStorageKey(projectId, "runContext"), context);
              setReadiness(null); setRun(null); setMatrix(null); setEvidence(null);
              setReportEvidence(null);
              setReport(null); setGate(null);
              save(projectStorageKey(projectId, "readiness"), null); save(projectStorageKey(projectId, "run"), null); save(projectStorageKey(projectId, "matrix"), null); save(projectStorageKey(projectId, "evidence"), null);
              save(projectStorageKey(projectId, "reportEvidence"), null);
              save(projectStorageKey(projectId, "report"), null); save(projectStorageKey(projectId, "gate"), null);
              setActiveView("running");
            }}
          />}
          {activeView === "running" && <Running
            projectId={projectId}
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
                const nextGate = await request(pathFor(projectId, "/release-decision"), { method: "POST", body: JSON.stringify({ report: value }) });
                setGate(nextGate); save(projectStorageKey(projectId, "gate"), nextGate);
                await refreshProject(projectId);
                setActiveView("report");
                setNotice({ kind: "success", text: `Product Evaluation Report 已生成；Gate: ${nextGate.decision}。` });
              } catch (error) { setNotice({ kind: "error", text: error.message }); }
              finally { setLoading(false); }
            }}
            loading={loading}
          />}
          {activeView === "report" && <Report
            projectId={projectId}
            report={report}
            evidence={reportEvidence || report?.evidence}
            gate={gate}
            reportList={reportList}
            onOpen={openReport}
            onImport={importReport}
            onRefresh={async () => { try { await refreshProject(projectId); setNotice({ kind: "success", text: "报告列表已刷新。" }); } catch (error) { setNotice({ kind: "error", text: error.message }); } }}
            loading={loading}
          />}
        </div>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
