import React, { useEffect } from "react";
import { I, Status, Page, SectionHeading, EmptyState, Metric, PipelineStep } from "../components.jsx";

const PIPELINE = [
  { label: "Planning", icon: "sparkle" },
  { label: "Scenario Generation", icon: "layers" },
  { label: "Readiness", icon: "shieldCheck" },
  { label: "Execution", icon: "play" },
  { label: "Evidence", icon: "doc" },
  { label: "Report", icon: "file" },
];

const RING_R = 52;
const RING_C = 2 * Math.PI * RING_R;

function isActiveRun(run) {
  return run && !["completed", "failed", "error"].includes(String(run.status).toLowerCase());
}

const PIPELINE_KEYS = ["planning", "scenario_generation", "readiness", "execution", "evidence", "report"];

function normalizedStage(value) {
  return String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
}

function pipelineStates({ plan, readiness, run, matrix, evidence }) {
  if (!run) {
    return [
      plan ? "complete" : "pending",
      plan?.scenarios?.length ? "complete" : "pending",
      readiness?.status === "ready" ? "complete" : readiness ? "blocked" : "pending",
      readiness?.status === "ready" ? "pending" : "locked",
      "locked",
      "locked",
    ];
  }

  const events = run.events || [];
  const currentStage = normalizedStage(run.current_stage);
  const currentIndex = currentStage === "completed" ? PIPELINE_KEYS.length : PIPELINE_KEYS.indexOf(currentStage);
  const terminalFailure = ["failed", "error"].includes(String(run.status).toLowerCase());
  const hasEvidence = Boolean(evidence || matrix || run.evidence_bundle_ref || run.artifact?.conditions?.length);

  return PIPELINE_KEYS.map((stage, index) => {
    const event = [...events].reverse().find((item) => normalizedStage(item.stage) === stage);
    const eventStatus = String(event?.status || "").toLowerCase();
    if (eventStatus === "failed" || eventStatus === "error") return "blocked";
    if (eventStatus === "completed" || eventStatus === "passed" || eventStatus === "success") return "complete";
    if (stage === "planning" && plan) return "complete";
    if (stage === "scenario_generation" && plan?.scenarios?.length) return "complete";
    if (stage === "readiness" && (readiness?.status === "ready" || run.readiness_ref)) return "complete";
    if (stage === "evidence" && hasEvidence) return "complete";
    if (stage === "report" && run.report_ref && !terminalFailure) return "complete";
    if (stage === "report" && terminalFailure) return "blocked";
    if (currentIndex > index) return "complete";
    if (currentIndex === index || eventStatus === "running") return terminalFailure ? "blocked" : "running";
    return index < currentIndex ? "complete" : "pending";
  });
}

function progressValue(run, states) {
  const explicit = [run?.progress_percent, run?.progress].find((value) => typeof value === "number" && Number.isFinite(value));
  if (explicit != null) return Math.max(0, Math.min(100, Math.round(explicit <= 1 ? explicit * 100 : explicit)));
  if (!run) return null;
  const completed = states.filter((state) => state === "complete").length;
  const running = states.some((state) => state === "running") ? 0.5 : 0;
  return Math.round(((completed + running) / states.length) * 100);
}

function artifactStatus(condition) {
  const observations = condition?.observations || {};
  if (observations.oracle_verified === true && observations.target_completed !== false) return "passed";
  if (observations.oracle_verified === false || observations.target_completed === false) return "failed";
  return "review";
}

export default function Running({ projectId, plan, requestRecord, runContext, readiness, run, matrix, evidence, onReadiness, onStart, onRefresh, onPoll, onArtifacts, onReport, loading, demoMode = false, readOnlyNotice }) {
  const active = isActiveRun(run);

  // Silent polling while a run is in flight — keeps the live view fresh
  // without spamming notices or toggling the global loading state.
  useEffect(() => {
    if (!active || !onPoll) return undefined;
    const timer = setInterval(onPoll, 4000);
    return () => clearInterval(timer);
  }, [active, onPoll]);

  if (!plan) {
    return <EmptyState icon="pulse" title="当前没有运行中的评估" detail="请先在 New Evaluation 生成评测计划。Readiness 通过后才能启动目标执行。" />;
  }

  const runDone = run?.status === "completed";
  const runTerminal = run && ["completed", "failed", "error"].includes(String(run.status).toLowerCase());
  const states = pipelineStates({ plan, readiness, run, matrix, evidence });
  const percent = progressValue(run, states);
  const matrixConditions = matrix?.conditions || run?.artifact?.conditions || [];
  const evidenceConditions = evidence?.conditions || run?.artifact?.conditions || [];
  const conditionCount = matrixConditions.length || evidenceConditions.length || 0;
  const scope = plan.evaluation_scope || {};
  const headline = run ? run.status : readiness ? readiness.status : "not checked";

  return (
    <Page
      title="运行监控"
      kicker="受控执行 · CONTROLLED EVALUATION"
      intro="服务端拥有执行、事件状态、矩阵产物与 Evidence Bundle 引用。"
    >
      {/* Hero: identity + progress ring */}
      <section className="run-hero">
        {active && <div className="particles"><i /><i /><i /><i /><i /><i /></div>}
        <div className="progress-ring">
          <svg viewBox="0 0 120 120">
            <defs>
              <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="#5b8cff" />
                <stop offset="100%" stopColor="#a78bfa" />
              </linearGradient>
            </defs>
            <circle className="ring-track" cx="60" cy="60" r={RING_R} fill="none" strokeWidth="7" />
            <circle
              className="ring-fill"
              cx="60" cy="60" r={RING_R} fill="none" strokeWidth="7"
              strokeDasharray={RING_C}
              strokeDashoffset={RING_C * (1 - (percent ?? 0) / 100)}
            />
          </svg>
          <div className="ring-label"><strong>{percent == null ? "—" : `${percent}%`}</strong><span>Progress</span></div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <span className="eyebrow">{plan.component_type} / {plan.component_name}</span>
          <h2>{plan.evaluation_name}</h2>
          <p className="muted" style={{ margin: "0 0 12px", fontSize: 12.5 }}>
            Request <span className="mono">{requestRecord?.request_id || plan.change_id}</span> · Project <span className="mono">{projectId}</span>
          </p>
          <Status value={headline} />
        </div>
        {active && <span className="status status-info status-live"><span className="status-pulse" />Live</span>}
      </section>

      {/* Animated pipeline */}
      <div className="pipeline">
        {PIPELINE.map((step, index) => (
          <PipelineStep key={step.label} label={step.label} icon={step.icon} state={states[index]} />
        ))}
      </div>

      {/* Scenario contract + actions */}
      <section className="section-block">
        <SectionHeading label="Scenario Contract" title={`${plan.scenarios.length} 个已生成场景`} />
        <div className="capability-list">
          {plan.scenarios.map((scenario) => (
            <div className="scenario-row" key={scenario.scenario_id}>
              <div>
                <span className="component-type">{scenario.scenario_id}</span>
                <strong>{scenario.category}</strong>
                <span className="muted">
                  {scenario.input_contract?.requirements?.length
                    ? scenario.input_contract.requirements.map((item) => `${item.fixture_id} / ${item.availability}`).join(" | ")
                    : "无外部 fixture 依赖"}
                </span>
              </div>
            </div>
          ))}
        </div>

        <div className="button-row">
          <button className="secondary" onClick={() => demoMode ? readOnlyNotice?.() : onReadiness()} disabled={loading}><I name="shieldCheck" />运行 Readiness</button>
          <button className="primary" onClick={() => demoMode ? readOnlyNotice?.() : onStart()} disabled={loading || readiness?.status !== "ready"} title={readiness?.status !== "ready" ? "Readiness 未通过，无法启动" : ""}><I name="play" />启动评估</button>
          <button className="secondary" onClick={() => demoMode ? readOnlyNotice?.() : onRefresh()} disabled={loading || !run}><I name="refresh" />刷新状态</button>
          <button className="secondary" onClick={() => demoMode ? readOnlyNotice?.() : onArtifacts()} disabled={loading || !runTerminal}><I name="doc" />加载 Matrix / Evidence</button>
          <button className="primary" onClick={() => demoMode ? readOnlyNotice?.() : onReport()} disabled={loading || !runDone}><I name="file" />生成报告</button>
        </div>

        {readiness?.blocking_reasons?.length ? (
          <div className="blocking-list">
            {readiness.blocking_reasons.map((item) => <p key={item}>{item}</p>)}
          </div>
        ) : null}

        {run && (
          <div className="run-meta">
            <Metric label="Run" value={run.run_id} />
            <Metric label="Plan" value={plan.plan_id} />
            <Metric label="Stage" value={run.current_stage} />
            <Metric label="Versions" value={`${scope.baseline_version || "-"} → ${scope.candidate_version || "-"}`} />
            <Metric label="Scope" value={scope.scope_id || run.scope_id || "-"} />
            <Metric label="Provider" value={`${scope.provider || "-"} / ${scope.model || "-"}`} />
            <Metric label="Budget / timeout" value={`${scope.budget_usd == null ? "-" : `$${scope.budget_usd}`} / ${scope.timeout_seconds == null ? "-" : `${scope.timeout_seconds}s`}`} />
            <Metric label="Side effects" value={scope.side_effect_policy || "-"} />
            <Metric label="Conditions" value={conditionCount || "not loaded"} />
            <Metric label="Matrix ref" value={run.matrix_artifact_ref || "pending"} />
          </div>
        )}
      </section>

      {/* Matrix projection */}
      {matrixConditions.length ? (
        <section className="section-block" style={{ marginTop: 18 }}>
          <SectionHeading label="Interaction Matrix" title={`${matrixConditions.length} 条执行条件`} />
          <div className="stack-tight">
            {matrixConditions.map((condition) => (
              <div className="check-row artifact-row" key={condition.condition_id || `${condition.scenario_id}-${condition.condition_kind}`}>
                <Status value={artifactStatus(condition)} />
                <strong>{condition.label || `${condition.scenario_id} / ${condition.condition_kind}`}</strong>
                <span>{condition.category || condition.observations?.scenario_category || "-"} · {condition.evidence_refs?.length || 0} evidence refs</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {/* Evidence projection */}
      {evidenceConditions.length ? (
        <section className="section-block" style={{ marginTop: 18 }}>
          <SectionHeading label="Evidence Bundle" title="已封存的条件结果" />
          <div className="summary-grid evidence-summary-grid">
            <Metric label="Verified" value={evidence?.summary?.verified_condition_count ?? run?.artifact?.metrics?.verified_condition_count ?? "-"} />
            <Metric label="Passed" value={evidence?.summary?.passed_condition_count ?? run?.artifact?.metrics?.passed_condition_count ?? "-"} />
            <Metric label="Failed" value={evidence?.summary?.failed_condition_count ?? run?.artifact?.metrics?.failed_condition_count ?? "-"} />
            <Metric label="Cost" value={Number.isFinite(Number(evidence?.summary?.total_cost_usd ?? run?.artifact?.metrics?.total_cost_usd)) ? `$${Number(evidence?.summary?.total_cost_usd ?? run?.artifact?.metrics?.total_cost_usd).toFixed(6)}` : "-"} />
          </div>
          <div className="stack-tight">
            {evidenceConditions.map((condition) => (
              <div className="check-row artifact-row" key={condition.condition_id || `${condition.scenario_id}-${condition.label}`}>
                <Status value={artifactStatus(condition)} />
                <strong>{condition.label || condition.condition_id}</strong>
                <span>Latency {condition.observations?.latency_ms ?? "-"} ms · Cost ${Number(condition.observations?.cost_usd || 0).toFixed(6)} · {condition.evidence_refs?.length || 0} refs</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {/* Run events timeline */}
      {run?.events?.length ? (
        <section className="section-block" style={{ marginTop: 18 }}>
          <SectionHeading label="Run Events" title="服务端状态时间线" />
          <div className="stack-tight">
            {run.events.map((event) => (
              <div className="check-row" key={event.event_id}>
                <Status value={event.status} />
                <strong>{event.stage}</strong>
                <span>{event.detail}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </Page>
  );
}
