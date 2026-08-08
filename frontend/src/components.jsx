import React from "react";

/* ============ Inline SVG icon set (Linear-style stroke icons) ============ */

const PATHS = {
  overview: <><rect x="3" y="3" width="7.5" height="7.5" rx="1.8" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.8" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.8" /><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.8" /></>,
  flask: <><path d="M9.5 3h5" /><path d="M10 3v5.2L4.7 17.4A2 2 0 0 0 6.5 20.5h11a2 2 0 0 0 1.8-3.1L14 8.2V3" /><path d="M7.5 14.5h9" /></>,
  pulse: <><path d="M3 12h4l2.5-6 4 12L16 12h5" /></>,
  doc: <><path d="M6 2.5h8L19 8v13.5H6z" /><path d="M13.5 2.5V8H19" /><path d="M9 12.5h6M9 16h6" /></>,
  shield: <><path d="M12 2.8 4.5 5.6v6.1c0 4.6 3.1 7.7 7.5 9.5 4.4-1.8 7.5-4.9 7.5-9.5V5.6z" /></>,
  shieldAlert: <><path d="M12 2.8 4.5 5.6v6.1c0 4.6 3.1 7.7 7.5 9.5 4.4-1.8 7.5-4.9 7.5-9.5V5.6z" /><path d="M12 8v4.2" /><circle cx="12" cy="15.4" r="0.4" fill="currentColor" /></>,
  shieldCheck: <><path d="M12 2.8 4.5 5.6v6.1c0 4.6 3.1 7.7 7.5 9.5 4.4-1.8 7.5-4.9 7.5-9.5V5.6z" /><path d="m9 11.6 2.2 2.2 4-4.2" /></>,
  check: <path d="m4.5 12.5 5 5 10-11" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5.2l3.4 2" /></>,
  alert: <><path d="M12 3.5 2.8 19.5h18.4z" /><path d="M12 9.5v4.5" /><circle cx="12" cy="16.8" r="0.4" fill="currentColor" /></>,
  bolt: <path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12z" />,
  layers: <><path d="m12 3 9 4.8-9 4.8-9-4.8z" /><path d="m4.2 12.6 7.8 4.1 7.8-4.1" /><path d="m4.2 16.6 7.8 4.1 7.8-4.1" /></>,
  link: <><path d="M9.5 14.5 14.5 9.5" /><path d="M11 6.5 13 4.5a3.5 3.5 0 0 1 5 5l-2 2" /><path d="m13 17.5-2 2a3.5 3.5 0 0 1-5-5l2-2" /></>,
  wrench: <><path d="M14.5 6.5a4.2 4.2 0 0 1 5.6-4L17.4 5l2 2 2.6-2.6a4.2 4.2 0 0 1-5.6 5.6L8 18.4A2.1 2.1 0 1 1 5 15.4z" /></>,
  play: <path d="M7 4.8v14.4L19 12z" />,
  refresh: <><path d="M20 12a8 8 0 1 1-2.3-5.6" /><path d="M20 3.5v4h-4" /></>,
  upload: <><path d="M12 16V4.5" /><path d="m6.5 9.5 5.5-5.5 5.5 5.5" /><path d="M4 15v3.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V15" /></>,
  download: <><path d="M12 4.5V16" /><path d="m6.5 11 5.5 5.5L17.5 11" /><path d="M4 15v3.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V15" /></>,
  arrowRight: <><path d="M4 12h15.5" /><path d="m14 6.5 5.5 5.5-5.5 5.5" /></>,
  cube: <><path d="m12 2.8 8.2 4.6v9.2L12 21.2l-8.2-4.6V7.4z" /><path d="M12 21.2V12" /><path d="m3.8 7.4 8.2 4.6 8.2-4.6" /></>,
  sparkle: <><path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18l-1.8-5.4L4.5 10.8 10.2 9z" /><path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></>,
  gear: <><circle cx="12" cy="12" r="3.2" /><path d="M12 2.8v2.6M12 18.6v2.6M2.8 12h2.6M18.6 12h2.6M5.2 5.2l1.9 1.9M16.9 16.9l1.9 1.9M18.8 5.2l-1.9 1.9M7.1 16.9l-1.9 1.9" /></>,
  file: <><path d="M6 2.5h8L19 8v13.5H6z" /><path d="M13.5 2.5V8H19" /></>,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="3" /></>,
  lock: <><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" /></>,
  database: <><ellipse cx="12" cy="5.5" rx="8" ry="3" /><path d="M4 5.5v13c0 1.7 3.6 3 8 3s8-1.3 8-3v-13" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
  radar: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" fill="currentColor" /><path d="M12 12 18 6" /></>,
  branch: <><circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><circle cx="18" cy="8" r="2.5" /><path d="M6 8.5v7" /><path d="M18 10.5c0 4-5 3-8.5 5.5" /></>,
  beaker: <><path d="M9 3h6" /><path d="M10 3v6L5 19a1.8 1.8 0 0 0 1.6 2.7h10.8A1.8 1.8 0 0 0 19 19l-5-10V3" /></>,
};

export function I({ name, size, style }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      width={size}
      height={size}
      style={style}
      aria-hidden="true"
    >
      {PATHS[name] || PATHS.cube}
    </svg>
  );
}

export function FileButton({ id, accept, onChange, children, icon = "upload" }) {
  return (
    <>
      <label className="file-button" htmlFor={id}>
        <I name={icon} />
        {children}
      </label>
      <input
        id={id}
        className="sr-file-input"
        type="file"
        accept={accept}
        onChange={onChange}
      />
    </>
  );
}

export function CapIcon({ type }) {
  const icon = type === "skill_pair" ? "link" : type === "tool" ? "wrench" : "bolt";
  return <span className={`cap-icon cap-${type}`}><I name={icon} /></span>;
}

/* ============ Status pill with unified iconography ============ */

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
  return (
    <span className={`status status-${tone}${isLive ? " status-live" : ""}`}>
      {isLive ? <span className="status-pulse" /> : <I name={icon} />}
      {String(value)}
    </span>
  );
}

/* ============ Page scaffolding ============ */

export function Page({ title, kicker, intro, action, children }) {
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">{kicker}</span>
          <h1>{title}</h1>
          <p>{intro}</p>
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

export function SectionHeading({ label, title }) {
  return (
    <div className="section-heading">
      <span className="eyebrow">{label}</span>
      <h2>{title}</h2>
    </div>
  );
}

export function EmptyState({ title, detail, action, onClick, icon = "radar" }) {
  return (
    <div className="empty-state">
      <span className="empty-mark"><I name={icon} /></span>
      <h1>{title}</h1>
      <p>{detail}</p>
      {action && <button className="primary" onClick={onClick}>{action}<I name="arrowRight" /></button>}
    </div>
  );
}

export function Metric({ label, value }) {
  const text = String(value ?? "-");
  return (
    <div className="metric">
      <span className="eyebrow">{label}</span>
      <strong title={text}>{text.length > 30 ? `${text.slice(0, 27)}…` : text}</strong>
    </div>
  );
}

/* ============ Pipeline step (evaluation workflow) ============ */

const STEP_STATE_LABEL = { complete: "已完成", running: "进行中", blocked: "已阻断", pending: "待执行", locked: "未解锁" };

export function PipelineStep({ label, state, icon }) {
  const orbIcon = state === "complete" ? "check" : state === "blocked" ? "x" : state === "running" ? null : icon;
  return (
    <div className={`pipeline-step ${state}`}>
      <span className="pipeline-orb">
        {state === "running" ? <I name={icon || "pulse"} /> : <I name={orbIcon || "clock"} />}
      </span>
      <span>{label}</span>
      <small>{STEP_STATE_LABEL[state] || state}</small>
    </div>
  );
}

/* ============ Decision shield visual ============ */

export function DecisionShield({ decision }) {
  const d = (decision || "pending").toLowerCase();
  const kind = d === "block" ? "block" : d === "approve" ? "approve" : d === "review" ? "review" : "pending";
  const colors = {
    block: ["#ef4444", "#f87171", "#7f1d1d"],
    approve: ["#10b981", "#34d399", "#064e3b"],
    review: ["#f59e0b", "#fbbf24", "#78350f"],
    pending: ["#5b8cff", "#7aa5ff", "#1e2a52"],
  }[kind];
  return (
    <svg viewBox="0 0 120 120" className={`shield-glow-${kind}`} aria-hidden="true">
      <defs>
        <linearGradient id={`sg-${kind}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={colors[1]} />
          <stop offset="100%" stopColor={colors[0]} />
        </linearGradient>
        <radialGradient id={`sglow-${kind}`} cx="0.5" cy="0.42" r="0.6">
          <stop offset="0%" stopColor={colors[1]} stopOpacity="0.35" />
          <stop offset="100%" stopColor={colors[1]} stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="60" cy="56" r="48" fill={`url(#sglow-${kind})`} />
      <path
        d="M60 14 24 28v30c0 22 15.6 36.6 36 44 20.4-7.4 36-22 36-44V28z"
        fill={colors[2]}
        fillOpacity="0.35"
        stroke={`url(#sg-${kind})`}
        strokeWidth="2.4"
        strokeLinejoin="round"
      />
      <path d="M60 22 31 33v25c0 17.6 12.4 29.4 29 35.6 16.6-6.2 29-18 29-35.6V33z" fill="none" stroke={colors[1]} strokeOpacity="0.35" strokeWidth="1.2" />
      {kind === "block" && (
        <g stroke={colors[1]} strokeWidth="3" strokeLinecap="round">
          <path d="M60 42v20" />
          <circle cx="60" cy="72" r="1.6" fill={colors[1]} stroke="none" />
        </g>
      )}
      {kind === "approve" && (
        <path d="m46 58 10 10 20-21" fill="none" stroke={colors[1]} strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
      )}
      {kind === "review" && (
        <g stroke={colors[1]} strokeWidth="3" strokeLinecap="round">
          <circle cx="60" cy="56" r="13" fill="none" />
          <path d="m69.5 65.5 7 7" />
        </g>
      )}
      {kind === "pending" && (
        <g stroke={colors[1]} strokeWidth="3" strokeLinecap="round">
          <circle cx="60" cy="56" r="13" fill="none" />
          <path d="M60 48v8.5l6 3.5" />
        </g>
      )}
    </svg>
  );
}
