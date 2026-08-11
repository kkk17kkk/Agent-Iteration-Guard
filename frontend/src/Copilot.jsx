import React, { useEffect, useMemo, useRef, useState } from "react";
import { pathFor, projectDisplayName } from "./lib.js";

function CopilotMark({ compact = false }) {
  return <img className={compact ? "copilot-mark compact" : "copilot-mark"} src="/icons/agent-guard-logo.svg" alt="" aria-hidden="true" />;
}

function ActionCard({ response, busy, onConfirm, onCancel }) {
  const action = response.proposed_action;
  if (!action) return null;
  const request = action.request;
  const done = action.status !== "awaiting_confirmation";
  const statusLabel = {
    awaiting_confirmation: "待确认",
    executed: "已创建",
    cancelled: "已取消",
  }[action.status] || action.status;
  return (
    <div className={`copilot-action-card ${action.status}`} data-testid="copilot-action-card">
      <div className="copilot-action-title">
        <span>创建评估请求</span>
        <strong>{statusLabel}</strong>
      </div>
      <dl>
        <div><dt>评估对象</dt><dd>{request.component_type} / {request.component_name}</dd></div>
        {request.pair_members?.length > 0 && <div><dt>组合成员</dt><dd>{request.pair_members.join(" + ")}</dd></div>}
        <div><dt>变更类型</dt><dd>{request.change_type}</dd></div>
        <div><dt>基线版本</dt><dd>{request.baseline_version}</dd></div>
        <div><dt>候选版本</dt><dd>{request.candidate_version}</dd></div>
      </dl>
      {action.executed_request_id && <div className="copilot-execution-id">{action.executed_request_id}</div>}
      {!done && (
        <div className="copilot-action-controls">
          <button type="button" onClick={() => onCancel(action.action_id)} disabled={busy}>取消</button>
          <button type="button" className="confirm" onClick={() => onConfirm(action.action_id)} disabled={busy}>确认创建</button>
        </div>
      )}
    </div>
  );
}

export default function Copilot({
  projectId,
  intelligence,
  providers,
  activeView,
  request,
  onNavigate,
  onProjectRefresh,
}) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const bodyRef = useRef(null);
  const panelRef = useRef(null);
  const [panelOffset, setPanelOffset] = useState({ x: 0, y: 0 });
  const capabilities = intelligence?.capability_registry || [];
  const projectName = projectDisplayName(intelligence?.agent_manifest || {}, projectId);
  const availableProvider = providers.find((item) => item.role === "control_plane" && item.status === "available");

  const starters = useMemo(() => {
    const skills = capabilities.filter((item) => item.component_type === "skill").map((item) => item.name);
    const values = [];
    if (skills[0]) values.push(`评估技能“${skills[0]}”`);
    values.push("分析最近一次评估结果");
    if (skills[0]) values.push(`如何改进技能“${skills[0]}”？`);
    if (skills.length > 1) values.push(`分析“${skills[0]}”与“${skills[1]}”如何协作`);
    else values.push("这个项目具备哪些能力？");
    return values.slice(0, 4);
  }, [capabilities]);

  useEffect(() => {
    setMessages([]);
    setInput("");
    setPhase("");
  }, [projectId]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, phase]);

  const startPanelDrag = (event) => {
    if (event.button !== 0 || event.target.closest("button")) return;
    const panel = panelRef.current;
    if (!panel) return;
    event.preventDefault();
    const bounds = panel.getBoundingClientRect();
    const start = { x: event.clientX, y: event.clientY, offset: panelOffset };
    const move = (moveEvent) => {
      const deltaX = Math.max(8 - bounds.left, Math.min(moveEvent.clientX - start.x, window.innerWidth - 8 - bounds.right));
      const deltaY = Math.max(8 - bounds.top, Math.min(moveEvent.clientY - start.y, window.innerHeight - 8 - bounds.bottom));
      setPanelOffset({ x: start.offset.x + deltaX, y: start.offset.y + deltaY });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  };

  const conversation = messages
    .filter((item) => item.content)
    .slice(-10)
    .map((item) => ({ role: item.role, content: item.content }));

  const submit = async (raw) => {
    const text = String(raw ?? input).trim();
    if (!text || busy) return;
    if (!projectId || !intelligence) {
      setMessages((current) => [...current, { role: "user", content: text }, { role: "assistant", content: "请先加载一个真实项目，再向 Copilot 提问。" }]);
      setInput("");
      return;
    }
    const userMessage = { role: "user", content: text };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setBusy(true);
    setPhase("正在检索项目上下文");
    try {
      const response = await request(pathFor(projectId, "/copilot/messages"), {
        method: "POST",
        body: JSON.stringify({
          message: text,
          conversation,
          page_context: { active_view: activeView },
          provider_binding_id: availableProvider?.provider_binding_id,
        }),
      });
      setMessages((current) => [...current, { role: "assistant", content: response.message, response }]);
      setPhase(response.state === "awaiting_confirmation" ? "等待确认" : "");
    } catch (error) {
      setMessages((current) => [...current, { role: "assistant", content: error.message, error: true }]);
      setPhase("已阻止 / 出错");
    } finally {
      setBusy(false);
    }
  };

  const updateAction = async (actionId, operation) => {
    setBusy(true);
    setPhase(operation === "confirm" ? "正在创建评估请求" : "正在取消");
    try {
      const response = await request(pathFor(projectId, `/copilot/actions/${encodeURIComponent(actionId)}/${operation}`), { method: "POST" });
      setMessages((current) => [...current, { role: "assistant", content: response.message, response }]);
      if (operation === "confirm") await onProjectRefresh?.();
      setPhase(response.state === "cancelled" ? "已取消" : "已完成");
    } catch (error) {
      setMessages((current) => [...current, { role: "assistant", content: error.message, error: true }]);
      setPhase("已阻止 / 出错");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`copilot-dock ${open ? "open" : ""}`}>
      {open && (
        <section
          ref={panelRef}
          className="copilot-panel"
          role="dialog"
          aria-label="AIG 评估助手"
          data-testid="copilot-panel"
          style={{ "--copilot-offset-x": `${panelOffset.x}px`, "--copilot-offset-y": `${panelOffset.y}px` }}
        >
          <header className="copilot-panel-header" onPointerDown={startPanelDrag}>
            <span className="copilot-header-icon"><CopilotMark compact /></span>
            <div>
              <strong>AIG 评估助手</strong>
              <span>{projectId ? projectName : "未加载项目"}</span>
            </div>
            <button type="button" className="copilot-close" aria-label="关闭 Copilot" onClick={() => setOpen(false)}>×</button>
          </header>

          <div className="copilot-conversation" ref={bodyRef} aria-live="polite">
            {messages.length === 0 && (
              <div className="copilot-empty">
                <h2>想让 AIG 检查什么？</h2>
                <p>可询问当前项目、评估与报告，或准备一份需要你确认的评估请求。</p>
                <div className="copilot-starters">
                  {starters.map((starter) => <button type="button" key={starter} onClick={() => submit(starter)}>{starter}</button>)}
                </div>
              </div>
            )}
            {messages.map((item, index) => (
              <article key={`${item.role}-${index}`} className={`copilot-message ${item.role} ${item.error ? "error" : ""}`}>
                <span>{item.role === "user" ? "你" : "Copilot"}</span>
                <p>{item.content}</p>
                {item.response?.interpretation_notice && <small>{item.response.interpretation_notice}</small>}
                {item.response?.references?.length > 0 && (
                  <div className="copilot-references">
                    {item.response.references.map((reference) => (
                      <button type="button" key={`${reference.kind}-${reference.object_id}`} onClick={() => onNavigate(reference)}>
                        {reference.label}
                      </button>
                    ))}
                  </div>
                )}
                <ActionCard
                  response={item.response || {}}
                  busy={busy}
                  onConfirm={(id) => updateAction(id, "confirm")}
                  onCancel={(id) => updateAction(id, "cancel")}
                />
              </article>
            ))}
            {busy && <div className="copilot-thinking"><span /><span /><span /> {phase || "正在思考"}</div>}
          </div>

          <form className="copilot-composer" onSubmit={(event) => { event.preventDefault(); submit(); }}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
              }}
              placeholder="向 AIG 提问，或准备一项评估…"
              rows="2"
              disabled={busy}
            />
            <button type="submit" aria-label="发送给 Copilot" disabled={busy || !input.trim()}>↑</button>
          </form>
          <footer>查询立即执行 · 写入必须确认</footer>
        </section>
      )}

      <button
        type="button"
        className="copilot-launcher"
        aria-label={open ? "关闭 AIG Copilot" : "打开 AIG Copilot"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        data-testid="copilot-launcher"
      >
        <span className="copilot-launcher-core"><CopilotMark /></span>
      </button>
    </div>
  );
}
