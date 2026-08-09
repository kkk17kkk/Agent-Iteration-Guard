import React from "react";

const ICON_FILES = {
  overview: "grid",
  doc: "file-text",
  file: "file-text",
  shield: "agent-guard-logo",
  shieldAlert: "shield-alert",
  shieldCheck: "shield-check",
  alert: "triangle-alert",
  bolt: "sparkle",
  refresh: "pulse",
  arrowRight: "arrow-right",
  gear: "settings",
  beaker: "flask",
  cube: "cube",
};

export function I({ name, size = 18, style, className = "" }) {
  const file = ICON_FILES[name] || name;
  return <img className={`ui-icon ${className}`} src={`/icons/${file}.svg`} width={size} height={size} style={style} alt="" aria-hidden="true" />;
}

export function FileButton({ id, accept, onChange, children, icon = "upload", disabled = false }) {
  return (
    <>
      <label className={`file-button${disabled ? " disabled" : ""}`} htmlFor={disabled ? undefined : id}>
        <I name={icon} />
        {children}
      </label>
      <input id={id} className="sr-file-input" type="file" accept={accept} onChange={onChange} disabled={disabled} />
    </>
  );
}

export function CapIcon({ type }) {
  const icon = type === "skill_pair" ? "link" : type === "tool" ? "wrench" : "sparkle";
  return <span className={`cap-icon cap-${type}`}><I name={icon} size={20} /></span>;
}

const OK = ["ready", "complete", "completed", "passed", "pass", "approve", "approved", "supported", "success", "registered", "validated", "confirmed", "comparable", "stable", "live"];
const BAD = ["blocked", "block", "error", "failed", "fail", "violation", "regressed", "incompatible", "rejected"];
const WARN = ["review", "warning", "stale", "unresolved", "caution", "to-be-determined", "degraded"];
const INFO = ["running", "in-progress", "evaluating", "scanning", "pending-execution"];

export function statusTone(value) {
  const v = String(value ?? "").toLowerCase().replaceAll(" ", "-").replaceAll("_", "-");
  if (OK.includes(v)) return "ok";
  if (BAD.includes(v)) return "bad";
  if (WARN.includes(v)) return "warn";
  if (INFO.includes(v)) return "info";
  return "mute";
}

export function Status({ value }) {
  const tone = statusTone(value);
  const isLive = tone === "info";
  const icon = { ok: "check", bad: "shieldAlert", warn: "clock", mute: "clock" }[tone];
  return <span className={`status status-${tone}${isLive ? " status-live" : ""}`}>
    {isLive ? <span className="status-pulse" /> : <I name={icon} size={14} />}
    {String(value || "Pending")}
  </span>;
}

export function Page({ title, kicker, intro, action, children }) {
  return <div className="page">
    <div className="page-heading">
      <div><span className="eyebrow">{kicker}</span><h1>{title}</h1>{intro && <p>{intro}</p>}</div>
      {action}
    </div>
    {children}
  </div>;
}

export function SectionHeading({ label, title, detail }) {
  return <div className="section-heading"><div><span className="eyebrow">{label}</span><h2>{title}</h2></div>{detail && <span className="section-detail">{detail}</span>}</div>;
}

export function EmptyState({ title, detail, action, onClick, icon = "radar" }) {
  return <div className="empty-state"><span className="empty-mark"><I name={icon} size={32} /></span><h1>{title}</h1><p>{detail}</p>{action && <button className="primary" onClick={onClick}>{action}<I name="arrowRight" /></button>}</div>;
}

export function Metric({ label, value, tone = "" }) {
  const text = String(value ?? "-");
  return <div className={`metric ${tone ? `metric-${tone}` : ""}`}><span className="eyebrow">{label}</span><strong title={text}>{text.length > 34 ? `${text.slice(0, 31)}…` : text}</strong></div>;
}

const STEP_STATE_LABEL = { complete: "已完成", running: "进行中", blocked: "已阻断", pending: "待执行", locked: "未解锁" };

export function PipelineStep({ label, state, icon }) {
  const orbIcon = state === "complete" ? "check" : state === "blocked" ? "x" : state === "running" ? icon || "pulse" : icon || "clock";
  return <div className={`pipeline-step ${state}`}><span className="pipeline-orb"><I name={orbIcon} size={16} /></span><span>{label}</span><small>{STEP_STATE_LABEL[state] || state}</small></div>;
}

export function DecisionShield({ decision }) {
  const d = String(decision || "pending").toLowerCase();
  const file = d === "block" ? "shield-alert" : d === "approve" ? "shield-check" : "shield-alert";
  return <div className={`decision-shield decision-shield-${d}`}><I name={file} size={104} /></div>;
}
