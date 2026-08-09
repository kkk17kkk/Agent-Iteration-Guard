import React, { useEffect, useState } from "react";
import { I, CapIcon, Status, Page, SectionHeading, EmptyState, ProjectContextCard } from "../components.jsx";
import { pathFor } from "../lib.js";

const TYPE_CARDS = [
  { value: "skill", label: "Skill", desc: "单能力组件的触发、执行与交付质量" },
  { value: "skill_pair", label: "Skill Pair", desc: "两个 Skill 的交互、协同与冲突分析" },
  { value: "tool", label: "Tool", desc: "工具 schema 与调用行为回归矩阵" },
];

const CHANGE_TYPES = [
  { value: "add", zh: "新增能力" },
  { value: "remove", zh: "移除能力" },
  { value: "modify", zh: "修改能力" },
  { value: "replace", zh: "替换实现" },
];

const QUALITY_DIMENSION_GUIDE = [
  ["Trigger", "是否能在声明的输入条件下正确触发，并识别不应处理的请求。"],
  ["Execution", "执行过程是否遵守工具、Skill 或组件自身的调用约束。"],
  ["Delivery", "结果是否完整、结构清晰，并能直接支持用户完成任务。"],
  ["Boundary", "是否在能力边界内工作，遇到不确定情况能否明确说明。"],
  ["Reliability / Cost", "重复执行时是否稳定，以及资源消耗是否处于可接受范围。"],
];

function listValue(value) {
  return String(value || "").split("\n").map((item) => item.trim()).filter(Boolean).slice(0, 8);
}

function pairMembers(record) {
  return Array.isArray(record?.dependencies) ? record.dependencies.filter(Boolean).slice(0, 2) : [];
}

function temporaryPairName(first, second) {
  return [first, second].filter(Boolean).sort().join("__");
}

function StepFlow({ current }) {
  const steps = ["选择组件与版本", "定义产品问题", "执行契约与权限", "生成评测计划"];
  return (
    <div className="step-flow">
      {steps.map((label, index) => (
        <React.Fragment key={label}>
          {index > 0 && <span className="step-connector" />}
          <div className={`step-node ${index + 1 < current ? "done" : index + 1 === current ? "current" : ""}`}>
            <span className="step-index">{index + 1 < current ? <I name="check" size={12} /> : index + 1}</span>
            <span className="step-label">{label}</span>
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}

export default function NewEvaluation({ projectId, intelligence, projectHeader, providers, executionConfigs, knowledge = [], fixtureRoot, setFixtureRoot, request, setLoading, setNotice, onPlan, onConfigurationChanged, prefill, demoMode = false, readOnlyNotice, onOverview }) {
  const [candidateVersion, setCandidateVersion] = useState(intelligence?.latest_snapshot?.version || "");
  const snapshots = intelligence?.snapshot_history || [];
  const baselineSnapshot = intelligence?.baseline_snapshot;
  const candidateSnapshot = snapshots.find((item) => item.version === candidateVersion);
  const baselineCapabilities = baselineSnapshot?.capability_snapshot || [];
  const candidateCapabilities = candidateSnapshot?.capability_registry || [];
  const capabilityMap = new Map();
  [...baselineCapabilities, ...candidateCapabilities, ...(intelligence?.capability_registry || [])].forEach((item) => {
    const key = `${item.component_type}:${item.name}`;
    if (!capabilityMap.has(key)) capabilityMap.set(key, item);
  });
  const diffMap = new Map((intelligence?.latest_diff?.component_changes || []).map((item) => [`${item.component_type}:${item.component_name}`, item]));
  const capabilities = [...capabilityMap.values()].map((item) => {
    const inBaseline = baselineCapabilities.some((candidate) => candidate.component_type === item.component_type && candidate.name === item.name);
    const inCandidate = candidateCapabilities.some((candidate) => candidate.component_type === item.component_type && candidate.name === item.name);
    const diffStatus = diffMap.get(`${item.component_type}:${item.name}`)?.status;
    return { ...item, changeStatus: diffStatus === "changed" ? "changed" : diffStatus === "added" ? "added" : diffStatus === "removed" ? "removed" : inBaseline && inCandidate ? "unchanged" : inBaseline ? "removed" : "added" };
  });
  const [componentType, setComponentType] = useState(capabilities[0]?.component_type || "skill");
  const candidates = capabilities.filter((item) => item.component_type === componentType);
  const [componentName, setComponentName] = useState(candidates[0]?.name || "");
  const [changeType, setChangeType] = useState("modify");
  const [controlBindingId, setControlBindingId] = useState("");
  const [targetBindingId, setTargetBindingId] = useState("");
  const [executionConfigId, setExecutionConfigId] = useState("");
  const [responsibility, setResponsibility] = useState("");
  const [userJob, setUserJob] = useState("");
  const [description, setDescription] = useState("");
  const [expectedBehavior, setExpectedBehavior] = useState("");
  const [qualityDimensions, setQualityDimensions] = useState("");
  const [boundary, setBoundary] = useState("");
  const [pairSkillOne, setPairSkillOne] = useState("");
  const [pairSkillTwo, setPairSkillTwo] = useState("");
  const [toolModalOpen, setToolModalOpen] = useState(false);
  const [qualityInfoOpen, setQualityInfoOpen] = useState(false);
  const [comparability, setComparability] = useState(null);
  const [createdRequest, setCreatedRequest] = useState(null);
  const [credentialOptions, setCredentialOptions] = useState([]);
  const [providerSetupOpen, setProviderSetupOpen] = useState(false);
  const [runtimeSetupOpen, setRuntimeSetupOpen] = useState(false);
  const [runtimeDraft, setRuntimeDraft] = useState(null);
  const [providerForm, setProviderForm] = useState({ provider: "openai", model: "", base_url: "", credential_environment_variable: "OPENAI_API_KEY" });
  const [runtimeForm, setRuntimeForm] = useState({ name: "", entrypoint: "", interaction_command: "", oracle_command: "", oracle_id: "" });
  const selected = candidates.find((item) => item.name === componentName);
  const skillOptions = capabilities.filter((item) => item.component_type === "skill");
  const pairOptions = capabilities.filter((item) => item.component_type === "skill_pair");
  const selectedPair = componentType === "skill_pair"
    ? pairOptions.find((item) => {
      const members = pairMembers(item);
      return members.length === 2 && members.includes(pairSkillOne) && members.includes(pairSkillTwo) && pairSkillOne !== pairSkillTwo;
    })
    : null;
  const selectedPairMembers = [pairSkillOne, pairSkillTwo].filter(Boolean);
  const temporaryPair = componentType === "skill_pair" && selectedPairMembers.length === 2 && pairSkillOne !== pairSkillTwo
    ? {
      component_type: "skill_pair",
      name: temporaryPairName(pairSkillOne, pairSkillTwo),
      responsibility: `${pairSkillOne} 与 ${pairSkillTwo} 的组合交互。`,
      dependencies: selectedPairMembers,
      boundary: [],
      temporary: true,
    }
    : null;
  const selectedComponent = componentType === "skill_pair" ? (selectedPair || temporaryPair) : selected;
  const controlBindings = providers.filter((item) => item.role === "control_plane");
  const targetBindings = providers.filter((item) => item.role === "sut_native");
  const baselineVersion = intelligence?.baseline_snapshot?.baseline_version || "";
  const candidateAvailable = Boolean(candidateSnapshot) || candidateVersion === baselineVersion;
  const candidateComponent = componentType === "skill"
    ? candidateSnapshot?.capability_registry?.find((item) => item.component_type === componentType && item.name === selectedComponent?.name)
    : undefined;
  const pairMembersPresentInCandidate = componentType === "skill_pair" && selectedPairMembers.length === 2
    && selectedPairMembers.every((member) => candidateCapabilities.some((item) => item.component_type === "skill" && item.name === member));

  useEffect(() => {
    setCandidateVersion(intelligence?.latest_snapshot?.version || "");
    setComponentName("");
    setPairSkillOne("");
    setPairSkillTwo("");
    setControlBindingId("");
    setTargetBindingId("");
    setExecutionConfigId("");
    setComparability(null);
    setCreatedRequest(null);
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return undefined;
    let cancelled = false;
    request(pathFor(projectId, "/provider-credentials"))
      .then((value) => { if (!cancelled) setCredentialOptions(value); })
      .catch(() => { if (!cancelled) setCredentialOptions([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    const available = credentialOptions.find((item) => item.status === "available");
    if (!available || providerForm.provider === available.provider) return;
    setProviderForm((current) => ({
      ...current,
      provider: available.provider,
      credential_environment_variable: available.environment_variable,
    }));
  }, [credentialOptions, providerForm.provider]);

  useEffect(() => {
    if (componentType === "skill_pair") {
      const prefilledPair = pairOptions.find((item) => item.name === prefill?.componentName) || pairOptions[0];
      const members = pairMembers(prefilledPair);
      setPairSkillOne(members[0] || skillOptions[0]?.name || "");
      setPairSkillTwo(members[1] || skillOptions[1]?.name || skillOptions[0]?.name || "");
    } else if (prefill?.componentName && candidates.some((item) => item.name === prefill.componentName)) {
      setComponentName(prefill.componentName);
    } else {
      setComponentName(candidates[0]?.name || "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, componentType, candidateVersion]);

  useEffect(() => {
    if (prefill?.componentType) setComponentType(prefill.componentType);
    if (prefill?.changeType) setChangeType(prefill.changeType);
    if (prefill?.providerBindingId) setControlBindingId(prefill.providerBindingId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  useEffect(() => {
    if (selectedComponent) {
      setResponsibility(selectedComponent.responsibility);
      setDescription(selectedComponent.responsibility || `Evaluate ${selectedComponent.name}.`);
      setBoundary((selectedComponent.boundary || []).join("\n"));
      setExpectedBehavior(componentType === "skill_pair"
        ? "The pair produces a usable result and applies cross-component feedback where required.\nThe combined flow remains bounded and does not fabricate output at its boundary."
        : "The component completes its declared responsibility and returns a structured, usable result.");
      setQualityDimensions(componentType === "skill_pair"
        ? "capability_contribution\nsynergy_gain\ncoordination\nconflict\nreliability_cost"
        : "trigger\nexecution\ndelivery\nboundary");
      if (!userJob) setUserJob(`Help the user complete work supported by ${selectedComponent.name}.`);
    }
  }, [selectedComponent?.name, componentType]);
  useEffect(() => {
    if (!controlBindingId && controlBindings[0]) setControlBindingId(controlBindings[0].provider_binding_id);
    if (!executionConfigId && executionConfigs[0]) setExecutionConfigId(executionConfigs[0].config_id);
  }, [providers, executionConfigs]);
  useEffect(() => {
    const config = executionConfigs.find((item) => item.config_id === executionConfigId);
    if (config?.target_provider_binding_id) setTargetBindingId(config.target_provider_binding_id);
  }, [executionConfigId]);

  useEffect(() => {
    if (!projectId || !baselineVersion || !candidateVersion) {
      setComparability(null);
      return undefined;
    }
    if (candidateVersion === baselineVersion) {
      setComparability({ status: "comparable", checks: [], detail: "The first imported snapshot is used as the frozen capability-evaluation runtime." });
      return undefined;
    }
    let cancelled = false;
    request(`${pathFor(projectId, "/runtime-comparability")}?baseline_version=${encodeURIComponent(baselineVersion)}&candidate_version=${encodeURIComponent(candidateVersion)}`)
      .then((value) => { if (!cancelled) setComparability(value); })
      .catch((error) => { if (!cancelled) setComparability({ status: "unresolved", checks: [], detail: error.message }); });
    return () => { cancelled = true; };
    // request is an App callback recreated on render; project/version changes are the intended triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, baselineVersion, candidateVersion]);

  const createPlan = async () => {
    if (demoMode) { readOnlyNotice?.(); return; }
    if (!projectId || !selectedComponent || !description || !responsibility || !userJob) {
      setNotice({ kind: "error", text: "请选择组件，并填写产品描述、产品职责与用户任务。" }); return;
    }
    if (!candidateVersion) {
      setNotice({ kind: "error", text: "请选择一个已扫描的 Snapshot。首次导入可直接使用初始冻结 Snapshot 进行 capability evaluation。" }); return;
    }
    if (!candidateAvailable) {
      setNotice({ kind: "error", text: "候选版本尚未注册到 Project Intelligence，不能创建 Evaluation Request。" }); return;
    }
    if (!comparability || comparability.status !== "comparable") {
      setNotice({ kind: "error", text: `Runtime comparability: ${comparability?.status || "checking"}；请先解决阻断检查。` }); return;
    }
    setLoading(true); setNotice(null);
    try {
      const created = createdRequest || await request(pathFor(projectId, "/evaluations"), {
        method: "POST",
        body: JSON.stringify({ component_type: componentType, component_name: selectedComponent.name, pair_members: componentType === "skill_pair" ? selectedPairMembers : undefined, change_type: changeType, candidate_version: candidateVersion, baseline_version: baselineVersion, candidate_available: candidateAvailable, candidate_component_name: candidateComponent?.name || undefined }),
      });
      setCreatedRequest(created);
      if (!controlBindingId) {
        setProviderSetupOpen(true);
        setNotice({ kind: "success", text: `Evaluation Request ${created.request_id} 已创建。请先配置 Evaluation Model，再生成 Plan。` });
        return;
      }
      const requestId = created.request_id;
      const productDefinition = { component_type: componentType, component_name: selectedComponent.name, description, product_responsibility: responsibility, user_job: userJob, expected_behavior: listValue(expectedBehavior), quality_dimensions: listValue(qualityDimensions), boundary: listValue(boundary), definition_status: "declared" };
      const value = await request(pathFor(projectId, "/evaluations/plan"), {
        method: "POST",
        body: JSON.stringify({ evaluation_request_id: requestId, provider_binding_id: controlBindingId, target_provider_binding_id: targetBindingId || undefined, evaluation_name: `${selectedComponent.name} evaluation`, product_definition: productDefinition }),
      });
      onPlan(value, created, { executionConfigId, providerBindingId: controlBindingId, targetBindingId, productDefinition });
      setNotice({ kind: "success", text: "Evaluation Plan 已由后端生成并冻结。" });
    } catch (error) { setNotice({ kind: "error", text: error.message }); }
    finally { setLoading(false); }
  };

  const onboardProvider = async () => {
    if (demoMode) { readOnlyNotice?.(); return; }
    setLoading(true); setNotice(null);
    try {
      const value = await request(pathFor(projectId, "/provider-bindings/onboard"), { method: "POST", body: JSON.stringify(providerForm) });
      await onConfigurationChanged?.();
      setControlBindingId(value.provider_binding_id);
      setProviderSetupOpen(false);
      setNotice({ kind: "success", text: `Evaluation Model 已连接：${value.provider} / ${value.model}。` });
    } catch (error) { setNotice({ kind: "error", text: `模型配置失败：${error.message}` }); }
    finally { setLoading(false); }
  };

  const loadRuntimeDraft = async () => {
    setLoading(true); setNotice(null);
    try {
      const value = await request(`${pathFor(projectId, "/runtime-drafts")}?snapshot_version=${encodeURIComponent(candidateVersion)}`);
      setRuntimeDraft(value);
      setRuntimeForm({
        name: `${selectedComponent?.name || "project"} runtime`, entrypoint: value.entrypoint || "",
        interaction_command: (value.suggested_interaction_command || []).join(" "),
        oracle_command: (value.suggested_oracle_command || []).join(" "),
        oracle_id: value.suggested_oracle_command?.length ? "project-oracle" : "",
      });
      setRuntimeSetupOpen(true);
    } catch (error) { setNotice({ kind: "error", text: `无法生成 Runtime Draft：${error.message}` }); }
    finally { setLoading(false); }
  };

  const saveRuntimeDraft = async () => {
    if (demoMode) { readOnlyNotice?.(); return; }
    if (!runtimeDraft) return;
    setLoading(true); setNotice(null);
    try {
      const command = (value) => value.trim().split(/\s+/).filter(Boolean);
      const config = await request(pathFor(projectId, `/runtime-drafts/${runtimeDraft.draft_id}/save`), {
        method: "POST", body: JSON.stringify({ ...runtimeForm, interaction_command: command(runtimeForm.interaction_command), oracle_command: command(runtimeForm.oracle_command) }),
      });
      await onConfigurationChanged?.();
      setExecutionConfigId(config.config_id);
      setRuntimeSetupOpen(false);
      setNotice({ kind: "success", text: "Project Runtime 已保存，并绑定到当前 Snapshot。" });
    } catch (error) { setNotice({ kind: "error", text: `Runtime review 未通过：${error.message}` }); }
    finally { setLoading(false); }
  };

  const saveReusablePair = async () => {
    if (demoMode) { readOnlyNotice?.(); return; }
    if (!temporaryPair) return;
    setLoading(true);
    try {
      await request(pathFor(projectId, "/skill-pairs"), { method: "POST", body: JSON.stringify({ name: temporaryPair.name, members: selectedPairMembers }) });
      await onConfigurationChanged?.();
      setNotice({ kind: "success", text: "Skill Pair 已保存为可复用组合；本次评测选择保持不变。" });
    } catch (error) { setNotice({ kind: "error", text: `保存 Skill Pair 失败：${error.message}` }); }
    finally { setLoading(false); }
  };

  if (!intelligence) {
    return <EmptyState icon="flask" title="请先加载项目" detail="Project Intelligence 提供已注册组件、基线、候选 Snapshot、Provider 与执行契约。" />;
  }

  const controlBinding = controlBindings.find((item) => item.provider_binding_id === controlBindingId);
  const selectedConfig = executionConfigs.find((item) => item.config_id === executionConfigId);

  return (
    <Page
      title="新建评估"
      kicker="新建评估 · NEW EVALUATION"
      intro="从本项目扫描发现的组件中选择评测对象。首次导入可直接使用初始冻结 Snapshot；后续版本则进行基线与候选版本比较。"
      headingCard
      before={<ProjectContextCard {...projectHeader} onOverview={onOverview} />}
    >
      <StepFlow current={selectedComponent ? (responsibility && userJob ? 3 : 2) : 1} />

      <div className="stack">
        {/* Step 1 — component & versions */}
        <section className="section-block">
          <SectionHeading label="Step 1 · Evaluation Request" title="评估对象与版本" />
          <div className="choice-grid" style={{ marginBottom: 20 }}>
            {TYPE_CARDS.map((card) => (
              <button
                key={card.value}
                className={componentType === card.value ? "choice-card selected" : "choice-card"}
                onClick={() => card.value === "tool" ? setToolModalOpen(true) : setComponentType(card.value)}
              >
                <span className="choice-check"><I name="check" /></span>
                <CapIcon type={card.value} />
                <strong>{card.label}</strong>
                <small>{card.desc}</small>
              </button>
            ))}
          </div>
          <div className="form-layout">
            <div className="form-panel">
              {componentType === "skill_pair" ? (
                <>
                  <label className="field">Skill 1
                    <select aria-label="Skill 1" value={pairSkillOne} onChange={(event) => setPairSkillOne(event.target.value)}>
                      <option value="">选择第一个 Skill</option>
                      {skillOptions.map((item) => <option value={item.name} key={item.name}>{item.name} · {item.changeStatus}</option>)}
                    </select>
                  </label>
                  <label className="field">Skill 2
                    <select aria-label="Skill 2" value={pairSkillTwo} onChange={(event) => setPairSkillTwo(event.target.value)}>
                      <option value="">选择第二个 Skill</option>
                      {skillOptions.map((item) => <option value={item.name} key={item.name}>{item.name} · {item.changeStatus}</option>)}
                    </select>
                  </label>
                  <div className={`pair-resolution ${selectedPair ? "resolved" : temporaryPair ? "temporary" : "unresolved"}`}>
                    <span className="eyebrow">Skill Pair 评测对象</span>
                    <strong>{selectedPair?.name || temporaryPair?.name || "请选择两个不同的 Skill"}</strong>
                    <small>{selectedPair
                      ? "已匹配项目登记 Pair，沿用其依赖关系。"
                      : temporaryPair
                        ? "项目尚未登记该 Pair；本次将按这两个已注册 Skill 创建临时评测组合，不修改项目注册表。"
                        : "Skill Pair 可以由任意两个已注册 Skill 组成。"}</small>
                    {temporaryPair && <div className="button-row"><button type="button" className="secondary" onClick={() => setNotice({ kind: "success", text: "本次将作为临时 Skill Pair 使用，不写入注册表。" })}>仅本次使用</button><button type="button" className="secondary" onClick={saveReusablePair}>保存为可复用 Pair</button></div>}
                  </div>
                </>
              ) : (
                <label className="field">组件名称
                  <select value={componentName} onChange={(event) => setComponentName(event.target.value)}>
                    {candidates.map((item) => <option value={item.name} key={item.name}>{item.name} · {item.changeStatus}</option>)}
                  </select>
                </label>
              )}
              <div>
                <span className="field" style={{ display: "block", marginBottom: 7, fontSize: 12.5, color: "var(--muted)", fontWeight: 550 }}>变化类型</span>
                <div className="choice-grid four">
                  {CHANGE_TYPES.map((item) => (
                    <button
                      key={item.value}
                      className={changeType === item.value ? "choice-pill selected" : "choice-pill"}
                      onClick={() => setChangeType(item.value)}
                    >
                      {item.zh}<small>{item.value}</small>
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="form-panel">
              <label className="field">候选 Snapshot
                <select value={candidateVersion} onChange={(event) => setCandidateVersion(event.target.value)}>
                  <option value="">选择已扫描的候选版本</option>
                  {snapshots.map((item) => <option key={item.snapshot_id} value={item.version}>{item.version}{item.version === baselineVersion ? " (initial capability evaluation)" : ""}</option>)}
                </select>
              </label>
              <label className="field">Evaluation Model
                <select value={controlBindingId} onChange={(event) => setControlBindingId(event.target.value)}>
                  <option value="">选择已配置模型</option>
                  {controlBindings.map((item) => <option key={item.provider_binding_id} value={item.provider_binding_id}>{item.provider} / {item.model} / credential {item.status}</option>)}
                </select>
              </label>
              {!controlBindings.length && <button className="secondary" type="button" onClick={() => setProviderSetupOpen(true)}>配置 Evaluation Model</button>}
              <label className="field">Target Provider Binding
                <select value={targetBindingId} onChange={(event) => setTargetBindingId(event.target.value)}>
                  <option value="">无 target binding</option>
                  {targetBindings.map((item) => <option key={item.provider_binding_id} value={item.provider_binding_id}>{item.provider} / {item.model} / {item.status} · {item.provider_binding_id.slice(-6)}</option>)}
                </select>
              </label>
            </div>
          </div>
          {controlBinding && (
            <div className="provider-binding-facts">
              <span className="eyebrow">Selected control-plane metadata</span>
              <span>{controlBinding.provider} / {controlBinding.model} · {controlBinding.status}</span>
              <span>env: {controlBinding.expected_environment_variable || "未返回环境变量名"}</span>
              <span>hosts: {(controlBinding.allowed_hosts || []).join(", ") || "-"}</span>
              <span>budget: ${controlBinding.batch_budget_usd ?? "-"} · timeout: {controlBinding.timeout_seconds ?? "-"}s · calls: {controlBinding.max_model_calls ?? "-"}</span>
              <span className="muted">API Key remains in project `.env` / runtime environment; it is not read or stored by the GUI.</span>
            </div>
          )}
        </section>

        <section className="section-block admission-panel">
          <SectionHeading label="Admission" title="Runtime comparability 与组件边界" />
          <div className="admission-summary">
            <Status value={comparability?.status || "pending"} />
            <span className="muted">Candidate snapshot: <span className="mono">{candidateVersion || "-"}</span> · component: <span className="mono">{selectedComponent?.name || "-"}</span> · presence: {componentType === "skill_pair" ? (pairMembersPresentInCandidate ? "pair members present" : "member missing") : candidateComponent ? "present" : "removed"}</span>
          </div>
          {comparability?.checks?.length ? <div className="stack-tight admission-checks">{comparability.checks.filter((item) => item.status !== "passed").map((item) => <div className="check-row" key={item.name}><Status value={item.status} /><strong>{item.name}</strong><span>{item.detail}</span></div>)}</div> : <p className="form-hint">选择已注册 candidate Snapshot 后，页面会读取服务端 comparability checks。</p>}
        </section>

        {/* Step 2 — product framing + execution contract */}
        <section className="section-block">
          <SectionHeading label="Step 2 · Product Framing" title="定义产品问题" />
          <div className="form-layout">
            <div className="form-panel">
              <label className="field">产品描述 Description
                <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明这个组件为用户解决什么问题，例如：根据饮食限制生成可执行的菜谱建议。" />
              </label>
              <label className="field">产品职责 Product Responsibility
                <textarea value={responsibility} onChange={(event) => setResponsibility(event.target.value)} placeholder="说明组件负责完成什么，例如：读取用户偏好并返回结构化推荐。" />
              </label>
              <label className="field">用户任务 User Job
                <textarea value={userJob} onChange={(event) => setUserJob(event.target.value)} placeholder="描述用户要完成的任务，例如：我想获得一份低糖晚餐计划。" />
              </label>
              <label className="field">预期行为 Expected Behavior
                <textarea value={expectedBehavior} onChange={(event) => setExpectedBehavior(event.target.value)} placeholder="每行一个可验证行为，例如：识别限制条件；返回可执行结果；拒绝越界请求。" />
              </label>
            </div>
            <div className="form-panel">
              <label className="field">
                <span className="field-label-with-info">质量维度 Quality Dimensions <button type="button" className="info-button" aria-label="查看质量维度说明" onClick={() => setQualityInfoOpen(true)}><I name="info" size={15} /></button></span>
                <textarea value={qualityDimensions} onChange={(event) => setQualityDimensions(event.target.value)} placeholder="每行一个质量维度，例如：trigger；execution；delivery；boundary。" />
              </label>
              <label className="field">边界 Boundary
                <textarea value={boundary} onChange={(event) => setBoundary(event.target.value)} placeholder="每行一个评测边界，例如：不发送外部消息；不修改源代码；不伪造结果。" />
              </label>
              <label className="field">Project Runtime
                <select value={executionConfigId} onChange={(event) => setExecutionConfigId(event.target.value)}>
                  <option value="">稍后在 Readiness 前配置</option>
                  {executionConfigs.map((item) => <option key={item.config_id} value={item.config_id}>{item.name} / {item.oracle_id}</option>)}
                </select>
              </label>
              {!executionConfigs.length && <button className="secondary" type="button" onClick={loadRuntimeDraft}>Review detected runtime</button>}
              <label className="field">Fixture Root
                <input value={fixtureRoot} onChange={(event) => setFixtureRoot(event.target.value)} placeholder="可选的本地 fixture 根目录" />
              </label>
              <p className="form-hint">Runtime 从当前 Snapshot 的静态扫描生成 draft；不确定的 entrypoint、interaction adapter 或 Oracle 必须在保存前明确 review。</p>
            </div>
          </div>
        </section>

        {knowledge.length > 0 && (
          <section className="section-block">
            <SectionHeading label="Evaluation Knowledge" title="本次可复用的历史经验" />
            <p className="form-hint">以下内容来自已完成评测，只作为场景和风险提示，不是本次评测的 ground truth。</p>
            <div className="knowledge-grid">
              {knowledge.map((item, index) => (
                <article className="knowledge-card" key={item.knowledge_id || `${item.component_pattern || "knowledge"}-${index}`}>
                  <strong>{item.component_pattern || item.pattern || "historical pattern"}</strong>
                  <p>{item.summary || item.risk || item.recommended_dimension || "有来源的历史评测经验"}</p>
                  <span className="muted">{item.evidence_level || "derived"} · {item.sample_count ?? "-"} samples</span>
                </article>
              ))}
            </div>
          </section>
        )}

        {/* Step 3 — evaluation scope + launch */}
        <section className="section-block">
          <SectionHeading label="Step 3 · Evaluation Scope" title="运行权限范围确认" />
          <div className="form-layout">
            <div className="scope-list">
              <div className="scope-item"><I name="eye" /><span className="scope-key">项目源读取</span><span className="scope-val">read_only · 仅扫描，不修改 Agent 源码</span></div>
              <div className="scope-item"><I name="branch" /><span className="scope-key">运行版本</span><span className="scope-val">基线 <span className="mono">{baselineVersion || "—"}</span> + 候选 <span className="mono">{candidateVersion || "未选择"}</span></span></div>
              <div className="scope-item"><I name="flask" /><span className="scope-key">执行矩阵</span><span className="scope-val">{componentType === "skill_pair" ? "A-only / B-only / Combined" : componentType === "tool" ? "工具回归矩阵" : "由 Planner 决定"}</span></div>
              <div className="scope-item"><I name="database" /><span className="scope-key">Fixture 目录</span><span className="scope-val mono">{fixtureRoot || "未指定（Readiness 前需再次检查）"}</span></div>
            </div>
            <div className="scope-list">
              <div className="scope-item"><I name="gear" /><span className="scope-key">Evaluation Model</span><span className="scope-val">{controlBinding ? `${controlBinding.provider} / ${controlBinding.model}` : "待配置"}</span></div>
              <div className="scope-item"><I name="file" /><span className="scope-key">Project Runtime</span><span className="scope-val">{selectedConfig ? `${selectedConfig.name} / ${selectedConfig.oracle_id}` : "Plan 后、Run 前需要 review"}</span></div>
              <div className="scope-item"><I name="lock" /><span className="scope-key">外部副作用</span><span className="scope-val">denied · 本地/隔离运行，不连接支付、邮件、删除、部署</span></div>
            </div>
          </div>
          <div className="button-row" style={{ justifyContent: "flex-end" }}>
            <button className="primary" onClick={createPlan}><I name="sparkle" />创建 Evaluation / 生成 Plan<I name="arrowRight" /></button>
          </div>
          {createdRequest && !controlBindingId && (
            <p className="form-hint">已创建 Request：<span className="mono">{createdRequest.request_id}</span>。请配置 Evaluation Model 后继续生成 Plan。</p>
          )}
        </section>
      </div>
      {providerSetupOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setProviderSetupOpen(false)}>
          <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="provider-setup-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-card-head"><div><span className="eyebrow">MODEL SETUP</span><h2 id="provider-setup-title">Configure Evaluation Model</h2></div><button className="icon-button" aria-label="关闭" onClick={() => setProviderSetupOpen(false)}><I name="x" /></button></div>
            <p className="form-hint">API Key 不会显示或保存到浏览器；服务端只检查所选环境变量是否可用，并保存模型协议、endpoint、model 与 planning limits。</p>
            <label className="field">Provider<select value={providerForm.provider} onChange={(event) => { const provider = event.target.value; const credential = credentialOptions.find((item) => item.provider === provider)?.environment_variable || ""; setProviderForm((current) => ({ ...current, provider, credential_environment_variable: credential })); }}><option value="openai">OpenAI compatible</option><option value="deepseek">DeepSeek</option><option value="vllm">vLLM</option></select></label>
            <label className="field">Model<input value={providerForm.model} onChange={(event) => setProviderForm((current) => ({ ...current, model: event.target.value }))} placeholder="例如 gpt-4.1-mini" /></label>
            <label className="field">Endpoint（可选）<input value={providerForm.base_url} onChange={(event) => setProviderForm((current) => ({ ...current, base_url: event.target.value }))} placeholder="https://…/v1" /></label>
            <label className="field">Credential environment variable<input value={providerForm.credential_environment_variable} onChange={(event) => setProviderForm((current) => ({ ...current, credential_environment_variable: event.target.value }))} /></label>
            <p className="form-hint">{credentialOptions.find((item) => item.environment_variable === providerForm.credential_environment_variable)?.status === "available" ? "Credential detected by server." : "Credential availability will be checked by server; its value is never returned."}</p>
            <div className="modal-card-actions"><button className="primary" onClick={onboardProvider}>Save & Continue</button></div>
          </section>
        </div>
      )}
      {qualityInfoOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setQualityInfoOpen(false)}>
          <section className="modal-card quality-info-modal" role="dialog" aria-modal="true" aria-labelledby="quality-info-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-card-head"><div><span className="eyebrow">QUALITY DIMENSIONS</span><h2 id="quality-info-title">质量维度说明</h2></div><button type="button" className="icon-button" aria-label="关闭质量维度说明" onClick={() => setQualityInfoOpen(false)}><I name="x" /></button></div>
            <p className="form-hint">可按项目真实风险选择维度；每行一个维度，提交时会作为评测计划的输入。</p>
            <div className="quality-dimension-list">
              {QUALITY_DIMENSION_GUIDE.map(([name, detail]) => <div className="quality-dimension-item" key={name}><strong>{name}</strong><p>{detail}</p></div>)}
            </div>
          </section>
        </div>
      )}
      {runtimeSetupOpen && runtimeDraft && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setRuntimeSetupOpen(false)}>
          <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="runtime-setup-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-card-head"><div><span className="eyebrow">RUNTIME REVIEW</span><h2 id="runtime-setup-title">Review detected project runtime</h2></div><button className="icon-button" aria-label="关闭" onClick={() => setRuntimeSetupOpen(false)}><I name="x" /></button></div>
            <p className="form-hint">Detected: {runtimeDraft.language} · {runtimeDraft.package_manager || "package manager unresolved"} · dependencies: {(runtimeDraft.dependency_files || []).join(", ") || "none"}.</p>
            {runtimeDraft.unresolved_fields?.length > 0 && <p className="form-hint">Needs review: {runtimeDraft.unresolved_fields.join("; ")}.</p>}
            <label className="field">Runtime name<input value={runtimeForm.name} onChange={(event) => setRuntimeForm((current) => ({ ...current, name: event.target.value }))} /></label>
            <label className="field">Entrypoint<input value={runtimeForm.entrypoint} onChange={(event) => setRuntimeForm((current) => ({ ...current, entrypoint: event.target.value }))} /></label>
            <label className="field">Interaction adapter command<input value={runtimeForm.interaction_command} onChange={(event) => setRuntimeForm((current) => ({ ...current, interaction_command: event.target.value }))} placeholder="python evaluate.py" /></label>
            <label className="field">Independent Oracle command<input value={runtimeForm.oracle_command} onChange={(event) => setRuntimeForm((current) => ({ ...current, oracle_command: event.target.value }))} placeholder="python oracle.py" /></label>
            <label className="field">Oracle ID<input value={runtimeForm.oracle_id} onChange={(event) => setRuntimeForm((current) => ({ ...current, oracle_id: event.target.value }))} /></label>
            <p className="form-hint">Commands are reviewed source-package commands. Server-owned cache, paths and run root are generated after save; no target code has run during discovery.</p>
            <div className="modal-card-actions"><button className="primary" onClick={saveRuntimeDraft}>Save Project Runtime</button></div>
          </section>
        </div>
      )}
      {toolModalOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setToolModalOpen(false)}>
          <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="tool-modal-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-card-head">
              <div>
                <span className="eyebrow">Tool Regression / Deferred</span>
                <h2 id="tool-modal-title">Tool 回归评测建设中</h2>
              </div>
              <button className="icon-button" aria-label="关闭" onClick={() => setToolModalOpen(false)}><I name="x" /></button>
            </div>
            <p className="large-copy">当前 v1 已保留 Tool 选项，但 Tool Planner、Runner 与 Oracle 闭环尚未接入。此入口不会创建 Evaluation Request，也不会修改当前选择。</p>
            <div className="modal-card-actions">
              <button className="primary" onClick={() => setToolModalOpen(false)}>知道了</button>
            </div>
          </section>
        </div>
      )}
    </Page>
  );
}
