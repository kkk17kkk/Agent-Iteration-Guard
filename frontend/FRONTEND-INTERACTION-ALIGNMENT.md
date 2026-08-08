# AIG v1.0 前端交互与后端接口对齐模板

> 状态：交互设计稿，仅用于前端实现前对齐；本次不修改页面代码。
>
> 适用范围：首页（Project Overview）与 New Evaluation。Running、Report 只定义本轮需要的交接状态，不在本文扩展完整页面设计。

## 0. 对齐结论

AIG 前端是本地评测工作台，不承担 Planner、Scenario Generator、Oracle、Analysis Agent 或 Release Decision Gate 的业务判断。页面只负责：

1. 让用户上传/接入 Agent 项目并查看 Project Intelligence。
2. 让用户确认本次评测的项目版本、组件和权限范围。
3. 在用户输入组件不存在、版本不可比、运行条件不可复现时立即阻断。
4. 调用后端生成 Evaluation Plan，并展示后端返回的状态、证据引用和阻断原因。

页面不得：

- 在浏览器内自行生成 scenario、替代后端判断能力是否提升，或把临时 Pair 伪装成项目已登记关系。
- 把本地前端校验当作最终验证；最终以 `POST /evaluations`、Runtime Comparability 和 Readiness 的后端结果为准。
- 将 `unresolved`、`blocked` 或缺少 Oracle evidence 解释成 Agent 失败。
- 在后端尚未提供通用 Planner/Execution API 时，用 Pair 页面逻辑伪装 Skill 或 Tool 已经可以执行。

## 1. 用户心智模型

用户只需要回答两个问题：

1. **我正在评估哪个 Agent 的哪个版本变化？**
2. **我允许 AIG 在什么边界内运行这次评测？**

页面术语统一如下：

| 用户看到的概念 | 后端概念 | 说明 |
| --- | --- | --- |
| Agent 项目 | `project_id` + `ProjectIntelligence` | 被评估的 Agent，不等同于 AIG 自身项目 |
| 项目版本 | `AgentSnapshot.version` | 一次不可变的扫描/注册结果 |
| 基线 | `baseline_snapshot.baseline_version` | 首次接入后冻结，不可被候选版本覆盖 |
| 候选版本 | `snapshot_history[].version` | 用户上传的新版本，必须已经被扫描并登记 |
| 能力组件 | `CapabilityRecord` | `skill`、`skill_pair`、`tool` |
| 评测知识 | `EvaluationKnowledge` | 历史评测经验，不是 Agent 长期记忆 |
| 运行权限范围 | UI 的显式执行授权摘要 | v1 本地操作员确认；当前后端尚未持久化完整权限对象 |

## 2. 页面共享状态模型

前端应在页面之间传递结构化状态，不依赖自由文本或只保存一个 `project_id`。

```json
{
  "project_context": {
    "project_id": "lighttable",
    "intelligence_fingerprint": "sha256...",
    "baseline_version": "v1.0",
    "candidate_version": "v1.1",
    "latest_scan_id": "scan_...",
    "latest_diff_id": "diff_..."
  },
  "evaluation_scope": {
    "component_type": "skill_pair",
    "component_name": "recipe_planning+nutrition_check",
    "change_type": "modify",
    "baseline_version": "v1.0",
    "candidate_version": "v1.1",
    "fixture_root": "D:/codexdata/fixtures/lighttable",
    "provider_binding_id": "binding_...",
    "target_execution": "baseline_candidate_combined",
    "external_side_effects": "denied"
  }
}
```

### 2.1 评测权限范围

New Evaluation 必须在提交前展示一块可确认的 `Evaluation Scope`，至少包括：

| 权限项 | 默认值 | UI 行为 | 后端事实 |
| --- | --- | --- | --- |
| 项目源读取 | `read_only` | 展示“仅扫描，不修改 Agent 源码” | Scanner 当前是只读扫描 |
| 运行版本 | 基线 + 候选 | 显示两个版本及 fingerprint | `EvaluationRequest` 绑定 baseline/candidate |
| 执行矩阵 | Skill：由计划决定；Pair：A-only/B-only/Combined；Tool：工具回归矩阵 | 展示预期 Arm，不允许用户删除必要 Arm | Pair Plan 已定义 `pair_a_only`、`pair_b_only`、`pair_combined` |
| Fixture 目录 | 用户选择或填写本地 fixture root | 显示路径，Readiness 前再次检查 | `POST /evaluations/readiness` 的 `fixture_root` |
| Control-plane Provider | 用户选择已登记 binding | 显示 provider、model、host allowlist、预算、调用上限；不显示 secret | 当前只有按 ID 查询 binding 的 Service/API 能力 |
| 外部副作用 | `denied` | 固定显示“本地/隔离运行，不连接支付、邮件、删除、部署” | v1 安全边界 |

当前后端没有独立的权限对象或用户身份系统。因此这里的“权限”是本地操作员对本次评测边界的显式确认，不应在 UI 中宣称为多用户访问控制。后续若需要审计，应将 `evaluation_scope` 作为结构化字段加入 `EvaluationRequest`，并绑定到 Evidence Bundle。

## 3. 首页 / Project Overview 必须具备的交互

### 3.1 空项目状态：接入第一个 Agent

用户首次打开 AIG 时，不应先看到空的能力表，而应看到一个明确的 `Upload / Connect Agent Project` 主操作。

#### 表单字段

| 字段 | 必填 | 交互要求 | 对应后端字段 |
| --- | --- | --- | --- |
| Project ID | 是 | 自动由名称生成 slug，但允许用户修改；提交前检查非空 | URL 中的 `{project_id}` |
| Project name / purpose | 扫描声明可推导 | 扫描后从 `AgentManifest` 展示；不要让用户重复录入已发现事实 | `agent_manifest.agent_name`、`purpose` |
| Source kind | 是 | `repository`、`package`、`docker_image` 三选一 | `ProjectScanRequest.source_kind` |
| Agent source | 是 | 文件夹、压缩包或 Docker image ref；显示来源而不是 secret | `source_ref` |
| Version | 是 | 首次默认为用户输入的版本或 `baseline-v1`；不可为空 | `ProjectScanRequest.version` |
| Entrypoint | 否 | 可选覆盖扫描声明 | `entrypoint` |
| Runtime kind | 否 | `native_http`、`native_command`、`package`、`docker` | `runtime_kind` |
| Declaration file | 否 | 可选 `aig.project.json` / `project-registration.json` | `declaration_file` |
| Optional Benchmark Import | 否 | 支持一次选择一个或多个 JSON；只读取 summary，不执行 Benchmark | `BenchmarkEvidenceRequest.result` |

#### 提交流程

```text
选择来源
  -> 上传/建立服务器可见 source_ref
  -> POST /api/v1/projects/{project_id}/scan
  -> scan.status = ready ? 建立/更新 Project Intelligence : unresolved/failed
  -> ready 后可选 POST /api/v1/projects/{project_id}/benchmark-evidence
  -> 刷新首页并展示 Snapshot / Capability / Runtime
```

扫描结果的处理必须明确区分：

| `scan.status` | 首页反馈 | 是否允许进入 New Evaluation |
| --- | --- | --- |
| `ready` | “项目已建立 Project Intelligence” | 是，但还需有可用候选版本才能创建变更评测 |
| `unresolved` | 展示 `unresolved_reasons` 和相关 `findings`，提供补充 declaration / 重新扫描 | 否 |
| `failed` | 展示错误 finding 和重试入口 | 否 |

#### 当前后端接口边界

`POST /api/v1/projects/{project_id}/scan` 当前接受 JSON 中的 `source_ref`。它适合 CLI 或 AIG API 与扫描器在同一台机器的场景，但浏览器的 `File` 对象不能直接变成服务端可读路径。

因此，真正实现“浏览器上传项目”前必须确定以下一个通用方案：

- 增加 `POST /api/v1/projects/{project_id}/uploads` 的 multipart 上传接口，返回受控的 `upload_ref`，再由 `/scan` 消费；或
- 本地 GUI 明确要求用户填写 AIG API 可见的目录/压缩包路径，并在文案中称为“连接本地项目”，不称为浏览器上传。

不能在前端把浏览器本地路径伪造为 `source_ref`，也不能把文件内容塞进 `source_ref`。

### 3.2 已接入项目状态

项目加载后，首页必须同时提供“理解 Agent”和“发现变化”的两层信息。

#### 顶部项目上下文

- Agent 名称、purpose、source kind/source identity。
- Intelligence status：`registered`、`ready`、`stale`。
- Baseline version 与 Latest version。
- 最新扫描时间、source fingerprint、intelligence fingerprint。
- 主操作：`Upload New Version`、`New Evaluation`。

接口：

- `GET /api/v1/projects/{project_id}/intelligence`
- `GET /api/v1/projects/{project_id}/scans`

当前没有 `GET /api/v1/projects` 项目列表接口。首页的 Project Switcher 在接口补齐前只能使用手动 Project ID 或 localStorage；不要把旧的 `GET /api/v1/products` 误当作 Project Intelligence 列表。

#### Agent Snapshot 时间线

从 `intelligence.snapshot_history` 展示：

- baseline / candidate 标记。
- version、created_at、snapshot fingerprint。
- 当前版本是否为 latest。
- 点击版本后查看该版本的 component registry 与 runtime profile。

新版本接入后，`POST /scan` 会在已有 Intelligence 上登记候选 Snapshot，并返回 `snapshot_diff`。

#### Changed Components 推荐区

从 `intelligence.latest_diff.component_changes` 生成推荐，不写死 LightTable 的 Skill 名称：

| diff status | 推荐文案 | 预填 New Evaluation |
| --- | --- | --- |
| `added` | “检测到新增组件，建议评估新增能力” | `change_type=add` |
| `changed` | “检测到组件变化，建议做回归评测” | `change_type=modify` |
| `removed` | “检测到组件移除，建议验证能力损失” | `change_type=remove` |
| `unchanged` | 默认不推荐；可在完整 registry 中查看 | 不自动创建 |

推荐项必须保留 `component_type`、`component_name`、baseline/candidate fingerprint 和 `changed_fields`，点击后完整带入 New Evaluation。推荐只是提效，不是强制选择；用户仍可手动输入同一 registry 中的组件。

#### Capability Registry

必须支持：

- 按 `skill`、`skill_pair`、`tool` 筛选。
- 展示 name、responsibility、dependencies、boundary、status、source_refs。
- `skill_pair` 展示恰好两个成员，并能跳转到成员详情。
- 对本次 diff 中 `added/changed/removed` 的组件显示状态标签。
- 对不存在或 `rejected/stale` 的组件不提供可用的 New Evaluation CTA。

接口：`GET /api/v1/projects/{project_id}/intelligence`。

#### Runtime 与历史评测知识

首页不应把运行配置和 Evaluation Knowledge 混成“Agent Memory”。应分开显示：

1. **Runtime Profile**：entrypoint、runtime kind、dependencies、model configuration、fixture 数量、trace/reset contract。
2. **Evaluation Knowledge**：common risks、recommended dimensions、scenario templates、source evaluation IDs、evidence level、sample count。

接口：

- `GET /api/v1/projects/{project_id}/evaluation-knowledge`
- `GET /api/v1/projects/{project_id}/benchmark-evidence`
- `GET /api/v1/projects/{project_id}/runtime-preflight?version={version}`

Benchmark Evidence 只作为 Release Summary 的辅助证据展示，例如 `Before 70% / After 75%`；首页不能把它渲染成 AIG 已执行 Benchmark。

### 3.3 新版本接入后的首页状态机

```text
No Project
  -> Scan in progress
      -> unresolved / failed  [停留首页，修复输入]
      -> ready
          -> baseline snapshot  [建立 Intelligence]
          -> candidate snapshot [展示 diff + changed recommendations]
                              -> New Evaluation
```

## 4. New Evaluation 必须具备的交互

New Evaluation 不是一个只填 Pair 的表单，而是一个受约束的五步流程。用户可以回退修改输入，但一旦后端生成 Evaluation Plan，页面必须把计划视为冻结对象。

### Step 1：确认项目、版本与评测权限

页面顶部固定显示：

- Project ID / Agent name。
- Baseline version（只读，来自 `baseline_snapshot`）。
- Candidate version（下拉选择已登记 Snapshot；禁止输入尚未扫描的任意字符串）。
- Candidate 的 source fingerprint 与 latest diff。
- Runtime comparability 状态：`comparable`、`incompatible`、`unresolved`。

接口：

```text
GET /api/v1/projects/{project_id}/intelligence
GET /api/v1/projects/{project_id}/runtime-preflight?version={candidate_version}
GET /api/v1/projects/{project_id}/runtime-comparability
  ?baseline_version={baseline_version}
  &candidate_version={candidate_version}
```

交互规则：

- `incompatible` 或 `unresolved` 时，显示具体 checks 和 evidence_refs，禁止进入创建阶段。
- baseline/candidate 不同项目或版本不存在时，不发起 `POST /evaluations`。
- candidate 尚未通过扫描并登记时，不允许用前端的 `candidate_available=true` 代替事实。
- 运行权限摘要必须明确“仅读取项目源、只运行所选两版本、fixture root 限定在本地路径、外部副作用禁止”。

### Step 2：选择组件类型与组件名称

#### 组件类型

提供三个同级选择：

- `Skill`
- `Skill Pair`
- `Tool`

选择类型后，组件输入框只展示该类型的 registry 建议。用户可以自己输入，但输入框下方必须即时给出状态：

| 状态 | 触发条件 | UI 行为 |
| --- | --- | --- |
| `recommended` | `latest_diff` 中为 `added/changed` | 显示“检测到版本变化，建议评估” |
| `registered` | 在 baseline/candidate 适用 Snapshot 中找到精确组件 | 允许继续 |
| `unchanged` | 组件存在但本次 diff 未变化 | 允许继续，但提示“未检测到版本变化，请确认是否仍要评估” |
| `removed` | 只存在于 baseline 且 change type 预计为 remove | 允许继续，必须选择 `remove` |
| `not_found` | 两个适用 Snapshot 都不存在 | 在输入框下方显示“未找到该模块，请从当前项目注册组件中选择”；禁止继续 |
| `ambiguous` | 名称或类型匹配不唯一 | 要求用户从建议项中选择精确记录 |

建议项来源：

- `intelligence.capability_registry`。
- `intelligence.snapshot_history[].capability_registry` 的 union，用于识别被移除组件。
- `intelligence.latest_diff.component_changes`，用于标记 changed/added/removed。

最终提交前仍必须由后端再次判断。当前已有接口：

```text
POST /api/v1/projects/{project_id}/evaluations
```

请求核心字段：

```json
{
  "component_type": "skill",
  "component_name": "recipe_planning",
  "change_type": "modify",
  "candidate_version": "v1.1",
  "baseline_version": "v1.0",
  "candidate_available": true,
  "candidate_component_name": "recipe_planning"
}
```

`candidate_available` 只能由已登记候选 Snapshot 或后端拥有的候选 Artifact 事实推导；前端不能无条件写成 `true`。

#### Skill Pair 特殊交互

Skill Pair 支持两种明确状态：

- 已登记 Pair：从项目 `CapabilityRecord.dependencies` 读取两个成员，沿用登记 Pair 名称。
- 临时 Pair：用户从项目已有 `skill` 中选择任意两个不同成员。前端展示确定性 Pair 名称，并在 `EvaluationRequest.pair_members` 中提交两个成员；后端验证成员存在后生成临时评测目标，不把它写入项目的 Capability Registry。

临时 Pair 不能被展示成“已登记关系”，也不能绕过成员存在性、候选 Snapshot 与 Runtime Comparability 校验。后续如需复用该关系，再由项目声明/登记流程将它加入 `skill_pair` Registry。

### Step 3：选择变化类型与候选版本事实

变化类型使用后端允许的四个值：

| 值 | 用户文案 | 前后版本约束 |
| --- | --- | --- |
| `add` | 新增能力 | baseline 不存在，candidate 存在 |
| `remove` | 移除能力 | baseline 存在，candidate 不存在 |
| `modify` | 修改能力 | baseline 与 candidate 都存在 |
| `replace` | 替换实现/能力 | 当前模型要求两侧存在；候选名称必须与目标匹配，不能冒充自动 attribution |

页面应在选择 change type 后立即根据两个 Snapshot 的 union 做预检查，并显示“预期存在性 / 实际存在性”。后端最终校验失败时，将错误定位到当前字段，而不是跳转到空报告。

可能返回的关键错误：

| 错误码 | UI 提示 | 是否允许继续 |
| --- | --- | --- |
| `E_COMPONENT_NOT_FOUND` | 当前项目没有注册该组件 | 否 |
| `E_BASELINE_NOT_FOUND` | 基线版本不在 Project Intelligence 历史中 | 否 |
| `E_CANDIDATE_NOT_FOUND` | 候选版本尚未上传/扫描/登记 | 否 |
| `E_VERSION_NOT_COMPARABLE` | 变化类型与两版本组件存在性不一致 | 否 |
| `E_RUNTIME_NOT_COMPARABLE` | 两版本运行环境不能公平比较 | 否 |
| `E_RUNTIME_NOT_REPRODUCIBLE` | entrypoint/source_ref/execution requirements 不完整 | 否 |
| `E_CANDIDATE_COMPONENT_MISMATCH` | 候选 Artifact 指向了另一个组件 | 否 |

当前 API 将错误码和消息拼在 `detail` 字符串中。为了让 GUI 稳定定位字段，建议后端统一返回 `{code, message, field, evidence_refs}`；在该接口未改造前，前端只能解析已知的 `E_` 前缀，不能依赖整句自然语言。

### Step 4：定义产品问题

必须与后端 `ProductDefinition` 字段一一对应：

| UI 字段 | 后端字段 | 必填 | 交互说明 |
| --- | --- | --- | --- |
| 组件描述 | `description` | 是 | 默认带入 registry responsibility，用户可修订 |
| 产品职责 | `product_responsibility` | 是 | 用产品语言描述，不写内部实现 |
| 用户任务 | `user_job` | 是 | 用户最终要完成的决策/任务 |
| 预期行为 | `expected_behavior[]` | 建议 | 每行一个可观察行为 |
| 质量维度 | `quality_dimensions[]` | 建议 | Skill 默认提示 trigger/execution/delivery/boundary；Pair 默认提示 capability contribution/synergy gain/coordination/conflict/reliability cost；最终以 Planner 为准 |
| 边界 | `boundary[]` | 建议 | 约束冲突、缺信息、资源限制、风险条件 |
| Evidence refs | `evidence_refs[]` | 否 | 可引用历史报告或 benchmark evidence，不允许编造 |

用户修改产品描述不会直接修改 Project Intelligence；它只属于本次 Evaluation Request/Plan。

### Step 5：评测知识、Provider 与计划

在点击 `Generate Evaluation Plan` 前，页面应展示“本次会复用的历史评测知识”：

```text
GET /api/v1/projects/{project_id}/evaluation-knowledge
  ?component_pattern={user_selected_pattern_or_component_type}
```

展示：

- 命中的 `component_pattern`。
- common risks。
- recommended dimensions。
- scenario templates。
- evidence level、sample count、source evaluation IDs。
- “这些是历史经验，不是本次评测 ground truth”的说明。

#### Control-plane Provider

当前 New Evaluation 页面需要一个 `provider_binding_id`，且计划生成必须使用 `role=control_plane`。页面必须展示：

- provider / model。
- allowed hosts。
- batch budget、timeout、max model calls、max output tokens。
- 价格信息是否已验证。
- Secret 只从 API 运行环境读取，前端不接收、不存储、不回显。

当前 API 只有：

```text
POST /api/v1/projects/{project_id}/provider-bindings
```

没有 `GET provider-bindings`。在补齐列表接口前，GUI 只能让用户输入 binding ID，这不是最终交互。实现正式页面前建议增加：

```text
GET /api/v1/projects/{project_id}/provider-bindings
```

返回脱敏后的 binding metadata。

#### 计划生成与交接

当前可用的 Pair 计划接口：

```text
POST /api/v1/projects/{project_id}/evaluations/plan
```

请求至少包含：

```json
{
  "evaluation_request_id": "evaluation_request_...",
  "provider_binding_id": "binding_...",
  "evaluation_name": "Recipe planning interaction acceptance",
  "knowledge_pattern": "skill_pair",
  "product_definition": {
    "component_type": "skill_pair",
    "component_name": "recipe_planning+nutrition_check",
    "description": "...",
    "product_responsibility": "...",
    "user_job": "...",
    "expected_behavior": [],
    "quality_dimensions": [],
    "boundary": [],
    "definition_status": "declared"
  }
}
```

成功后必须保存并展示：

- `plan_id`、`status`、`planning_method`。
- `rationale`、`hypothesis`、`interaction_hypothesis`。
- 场景 category、prompt、goal、expected behavior、evidence requirements。
- `scenario_hash`、`scenario_provenance.hypothesis_source`、provider/model/request metadata、`frozen=true`。
- knowledge hit IDs。

然后调用：

```text
POST /api/v1/projects/{project_id}/evaluations/readiness
```

请求：

```json
{
  "evaluation_plan": { "...": "frozen EvaluationPlan" },
  "fixture_root": "D:/codexdata/fixtures/lighttable"
}
```

只有 `status=ready` 才能把用户带入 Running。`blocked` 必须留在当前流程或进入 Running 的阻断状态，并展示每个 scenario 的 `blocking_reasons`。

### 4.1 三种组件类型的前端可用性边界

| 类型 | Evaluation Request | 当前 Plan API | New Evaluation UI |
| --- | --- | --- | --- |
| Skill | `POST /evaluations` 支持 | 当前 `/evaluations/plan` 内部只构造 Skill Pair target；没有通用 Skill planner API | 可以选择、输入、预校验并创建 request；在通用 Planner API 补齐前，不显示“已可生成 Skill Plan” |
| Skill Pair | 支持 | 当前主线已支持 control-plane LLM 关系判断、场景生成和持久化计划 | 完整可用；必须展示 pair members、knowledge hit 和 frozen scenario provenance |
| Tool | `POST /evaluations` 支持 | 当前 `/evaluations/plan` 没有 Tool strategy dispatch；Tool Regression 主要由 CLI/模块提供 | 可以选择并校验；计划按钮应显示“Tool Planner API 待接入”，不能复用 Pair payload |

这里的限制是后端当前事实，不是前端隐藏功能。若产品要求三类都从 GUI 一键进入 Running，下一步必须先把 API 的 Planner dispatch 统一为 `component_type -> SkillStrategy / SkillPairStrategy / ToolStrategy`。

## 5. 首页与 New Evaluation 的接口矩阵

| 交互 ID | 页面动作 | 方法与接口 | 使用的核心响应 | 当前状态 |
| --- | --- | --- | --- | --- |
| H-01 | 加载项目详情 | `GET /api/v1/projects/{project_id}/intelligence` | Manifest、registry、runtime、baseline、snapshot history、latest diff | 已有 |
| H-02 | 扫描首次项目/候选版本 | `POST /api/v1/projects/{project_id}/scan` | `scan`、`registration`、`intelligence`、`snapshot_diff` | 已有；浏览器上传适配待补 |
| H-03 | 查看扫描历史 | `GET /api/v1/projects/{project_id}/scans` | Scan records、findings、unresolved reasons | 已有 |
| H-04 | Runtime 单版本预检 | `GET /api/v1/projects/{project_id}/runtime-preflight` | checks、status、fingerprint | 已有 |
| H-05 | 跨版本公平性检查 | `GET /api/v1/projects/{project_id}/runtime-comparability` | baseline/candidate preflight、checks、status | 已有 |
| H-06 | 导入可选 Benchmark | `POST /api/v1/projects/{project_id}/benchmark-evidence` | Benchmark evidence、integrity metadata | 已有；浏览器文件上传由 UI 读取 JSON |
| H-07 | 查看 Benchmark | `GET /api/v1/projects/{project_id}/benchmark-evidence` | 已导入 evidence | 已有 |
| H-08 | 查看 Evaluation Knowledge | `GET /api/v1/projects/{project_id}/evaluation-knowledge` | risks、dimensions、templates、provenance | 已有 |
| H-09 | 项目下拉列表 | `GET /api/v1/projects` | project IDs / names / statuses | 缺失；暂用手动 project ID，不用 products 接口冒充 |
| N-01 | 选择组件和版本 | H-01 + H-05 | registry、diff、comparability | 已有，需前端组合 |
| N-02 | 创建 Evaluation Request | `POST /api/v1/projects/{project_id}/evaluations` | validated request、request ID、runtime comparability | 已有 |
| N-03 | 读取已创建 Request | `GET /api/v1/projects/{project_id}/evaluations/{request_id}` | request status、versions、component | 已有 |
| N-04 | 读取 Provider bindings | `GET /api/v1/projects/{project_id}/provider-bindings` | 脱敏 binding metadata | 缺失；当前只能手填 ID |
| N-05 | 生成 Evaluation Plan | `POST /api/v1/projects/{project_id}/evaluations/plan` | frozen EvaluationPlan | 当前只支持 Skill Pair |
| N-06 | 计划 Readiness | `POST /api/v1/projects/{project_id}/evaluations/readiness` | scenario checks、blocking reasons | 已有 |
| N-07 | 持久化权限范围 | EvaluationRequest 的 `evaluation_scope` | source/versions/fixtures/provider/side-effects | 缺失；目前只能由 UI 临时展示 |
| N-08 | 执行完整矩阵 | 通用 execution/matrix API | Trial、Oracle、Evidence Bundle | 当前 GUI API 缺失，CLI 可执行 Pair matrix |

## 6. 统一的 CTA 门禁

### 首页

```text
canStartEvaluation =
  intelligence.status in {registered, ready}
  && candidate_version exists
  && candidate runtime is comparable
  && selected component resolves in baseline/candidate registry
```

### New Evaluation

```text
canCreateRequest =
  project loaded
  && baseline/candidate selected
  && component_type selected
  && exact component_name resolves
  && change_type matches component presence
  && runtime_comparability == comparable
  && product_definition.description
  && product_definition.product_responsibility
  && product_definition.user_job
```

```text
canGeneratePlan =
  request.status == validated
  && provider_binding.role == control_plane
  && selected component_type has a Planner API
```

```text
canEnterRunning =
  frozen_plan exists
  && readiness.status == ready
```

所有 CTA 的 disabled 原因必须可见，例如：

- “候选版本尚未扫描并登记”。
- “未找到 `nutrition_check`；请从当前项目 registry 选择已注册组件”。
- “baseline/candidate runtime 不可比：model_configuration 不一致”。
- “当前 Provider Binding 不是 control_plane”。
- “该组件类型的通用 Planner API 尚未接入”。

## 7. 与 CLI 的对齐检查

GUI 不应发明 CLI 没有的业务状态。两者应共享相同的 domain model 和 Service：

| 用户操作 | CLI 事实入口 | GUI 入口 |
| --- | --- | --- |
| 扫描项目 | `agentguard project scan` | `POST /scan` |
| 查看 Project Intelligence | `agentguard project get` | `GET /intelligence` |
| Runtime 预检 | `agentguard project runtime-preflight` | `GET /runtime-preflight` |
| Runtime 对比 | `agentguard project runtime-compare` | `GET /runtime-comparability` |
| 创建评测请求 | `agentguard evaluation create` | `POST /evaluations` |
| 生成 Pair Plan | `agentguard evaluation plan` | `POST /evaluations/plan` |
| Readiness | `agentguard evaluation readiness` | `POST /evaluations/readiness` |
| Pair 完整矩阵 | `agentguard evaluation interaction-matrix` | 后续增加 execution API 后再接 Running |
| Benchmark Import | `agentguard benchmark import` | `POST /benchmark-evidence` |
| Evaluation Knowledge | `agentguard memory list` | `GET /evaluation-knowledge` |

特别注意：CLI 当前也明确只允许 `evaluation plan` 处理 `skill_pair`。GUI 不应绕过这一事实。

## 8. 实现前必须先解决的后端对齐项

按阻塞程度排序：

### P0

1. **浏览器上传契约**：增加 upload/ref 适配，或明确 GUI 只连接 API 所在机器的路径。
2. **通用 Planner dispatch**：同一个 Plan API 根据 `component_type` 调用 Skill、Skill Pair、Tool strategy；不能把 Pair builder 写在 API 路由里作为长期方案。
3. **Execution / Report API**：当前 GUI 能创建 Plan 和做 Readiness，但没有从 API 启动完整矩阵、查询 Evidence Bundle、读取 ProductEvaluationReport 的主线接口。
4. **Provider Binding 列表**：增加脱敏 `GET`，前端不应长期手填 binding ID。
5. **权限范围持久化**：把 `evaluation_scope` 写入 EvaluationRequest 或独立 immutable contract，供 Evidence Bundle 和 Gate 引用。

### P1

1. **项目列表接口**：提供 `GET /api/v1/projects`，返回项目状态、latest version、changed component count。
2. **结构化错误响应**：统一返回 `code/message/field/evidence_refs`，支持输入框下方精确提示。
3. **Scan 上传与 Benchmark Import 的统一文件边界**：前端只负责选择文件，后端负责保存 source fingerprint、大小、hash 和来源 metadata。
4. **Evaluation Request 预校验接口**：可选增加不落库的 validation endpoint；即使不增加，前端也必须保留 `POST /evaluations` 作为最终门禁。

## 9. 页面验收清单

### Project Overview

- [ ] 空项目时能进入接入流程，而不是只能手动填 project ID。
- [ ] 首次扫描 `ready/unresolved/failed` 三种状态都有清晰反馈。
- [ ] 新版本扫描后能显示 snapshot diff。
- [ ] `added/changed/removed` 组件能生成推荐，且推荐不是硬编码名称。
- [ ] 手动输入不存在的 Skill/Skill Pair/Tool 时，输入框下方立即提示并禁用 New Evaluation。
- [ ] Runtime Profile、Baseline Snapshot、Evaluation Knowledge、Benchmark Evidence 分区展示。
- [ ] 页面不显示 secret，不把 Benchmark Import 渲染为 AIG 执行结果。

### New Evaluation

- [ ] 能选择 `skill`、`skill_pair`、`tool`，并显示当前后端对每类的实际可用性。
- [ ] baseline/candidate 只能来自已登记 Snapshot。
- [ ] runtime comparability 未通过时不能创建 request。
- [ ] 输入组件不存在时无法进入下一步；后端 `E_COMPONENT_NOT_FOUND` 仍能回填为字段错误。
- [ ] change type 与前后版本组件存在性不一致时阻断。
- [ ] Skill Pair 展示两个成员，不从字符串猜 pair。
- [ ] 评测权限范围、fixture root、provider binding、外部副作用边界在提交前可见。
- [ ] Evaluation Knowledge 命中后可见 provenance，不把经验当作 ground truth。
- [ ] 计划生成后展示 frozen scenario/hash/provider metadata。
- [ ] Readiness 未通过时不进入可执行状态。
- [ ] 前端不伪造 Execution、Oracle、Report 或 Release Decision。

## 10. 下一步实施顺序

1. 先补齐 P0 API 契约：upload/ref、provider binding list、generic planner dispatch、execution/report boundary、evaluation scope。
2. 再实现首页：接入项目、扫描、Snapshot diff、组件推荐、Benchmark import。
3. 再实现 New Evaluation：三类组件选择、实时 registry 校验、版本/运行权限门禁、Request/Plan/Readiness 交接。
4. 最后接 Running 和 Report，且只消费 Evidence Bundle、ProductEvaluationReport 和 Deterministic Release Decision Gate 的后端结果。

## 11. “补齐 P0 API 契约”具体是什么意思

这里的“补齐”不是马上改前端，也不是为了增加接口数量，而是把 CLI 已经能够完成的核心动作变成 GUI 可以安全调用、查询和复核的稳定 HTTP 合同。每一项都要有明确的 request、response、状态、错误和证据边界。

### 11.1 upload/ref

**问题**：CLI 可以直接接收本地路径；浏览器只有 `File` 对象，不能把用户电脑路径直接交给 API。当前 `/scan` 只接受 `source_ref`，因此还没有真正的浏览器上传闭环。

**需要表达的能力**：

```text
Browser File / Docker ref
  -> AIG controlled upload or source registration
  -> immutable upload_ref / source_ref / source_fingerprint
  -> Project Scanner
```

**建议合同**：

```text
POST /api/v1/projects/{project_id}/uploads
GET  /api/v1/projects/{project_id}/uploads/{upload_id}
POST /api/v1/projects/{project_id}/scan
```

上传响应至少包含 `upload_id`、`source_kind`、`source_ref`、`size_bytes`、`source_fingerprint`、`status`。`scan` 消费的是受控 `upload_ref`，而不是用户任意提交的路径。Docker image 可以不上传，但必须将 image ref/digest 作为同样的 immutable source identity 记录下来。

**CLI 对齐**：`agentguard project scan --source ...` 与 GUI 都最终生成同一个 `ProjectScanRequest`，区别只有 source 如何到达 Scanner。

### 11.2 provider binding list

**问题**：CLI 可以读取用户指定的非 secret ProviderBinding JSON；当前 GUI 只能手填 `provider_binding_id`，后端也没有 `GET` 列表接口。

**需要表达的能力**：

```text
列出当前项目可用的 ProviderBinding metadata
  -> 用户选择 role=control_plane 的 binding
  -> GUI 显示 provider/model/budget/allowlist
  -> secret 仍只由后端运行环境读取
```

**建议合同**：

```text
GET /api/v1/projects/{project_id}/provider-bindings
GET /api/v1/projects/{project_id}/provider-bindings/{binding_id}
```

响应只能包含脱敏 metadata：`binding_id`、`role`、`provider`、`model`、`allowed_hosts`、预算、调用上限、价格校验状态。不能返回 API key、环境变量值或完整 credential source。

**CLI 对齐**：CLI 的 `--binding` 文件和 API 返回的 binding metadata 必须反序列化为同一个 `ProviderBinding`；GUI 不应自己拼 Provider 配置。

### 11.3 generic planner dispatch

**问题**：当前 `POST /evaluations/plan` 的 API 路由直接调用 `build_skill_pair_evaluation_target`。CLI 也明确限制当前 plan entry point 只处理 `skill_pair`。但产品入口已经允许 Skill、Skill Pair、Tool 三类组件。

**需要表达的能力**：同一个 Evaluation Request 进入统一 Planner，由 `component_type` 和 `change_type` 选择策略：

```text
skill      -> SkillStrategy
skill_pair -> SkillPairStrategy
tool       -> ToolStrategy
```

**建议合同保持一个入口**：

```text
POST /api/v1/projects/{project_id}/evaluations/plan
```

请求使用已验证的 `evaluation_request_id`，后端从 Request 读取组件类型、版本和变化类型；用户提交的 `product_definition` 只补充产品问题，不允许前端改写 Planner 身份。响应统一为 `EvaluationPlan`，不返回 `SkillReport`、`ToolReport` 或 Pair 专属外壳。

**CLI 对齐**：`agentguard evaluation plan` 也应调用同一 Service dispatch，而不是 CLI 自己判断 Pair。未接入某类型策略时，CLI 和 GUI 都返回明确的 `E_PLANNER_NOT_AVAILABLE`，不能降级复用另一种策略。

### 11.4 execution/report boundary

**问题**：CLI 已经可以执行 `evaluation interaction-matrix`，生成 `InteractionMatrixArtifact`；但当前 API 没有启动矩阵、查询运行状态、读取 Evidence Bundle 或生成/读取 ProductEvaluationReport 的 v1 项目接口。现有 GUI 的 Running 只能做 Readiness，Report 只能导入本地 JSON 后调用 Gate。

**需要表达的能力**：将 CLI 的一次完整运行暴露为可查询的 Run，而不是让浏览器读取服务器临时文件或拼接 CLI 命令。

建议共享以下边界模型：

```text
EvaluationPlan
  -> EvaluationReadinessResult
  -> EvaluationRun
  -> InteractionMatrixArtifact / EvidenceBundle
  -> ProductEvaluationReport
  -> ReleaseDecisionGateResult
```

**建议合同**：

```text
POST /api/v1/projects/{project_id}/evaluations/{request_id}/runs
GET  /api/v1/projects/{project_id}/evaluations/{request_id}/runs
GET  /api/v1/projects/{project_id}/evaluations/{request_id}/runs/{run_id}
GET  /api/v1/projects/{project_id}/evaluations/{request_id}/runs/{run_id}/events
GET  /api/v1/projects/{project_id}/evaluations/{request_id}/runs/{run_id}/matrix
GET  /api/v1/projects/{project_id}/evaluations/{request_id}/runs/{run_id}/evidence
GET  /api/v1/projects/{project_id}/evaluations/{request_id}/runs/{run_id}/report
```

`POST runs` 的语义应等价于 CLI `evaluation interaction-matrix` 的必要输入：`plan_id`、target manifest/cache reference、fixture root、interaction name、evaluation ID、Oracle ID/type/version 和 target ProviderBinding reference。UI 不允许输入任意 Oracle command；Oracle contract 应来自项目登记或已批准的运行配置。

Run 响应至少要有：`run_id`、`status`、`current_stage`、`plan_id`、`readiness_ref`、`matrix_artifact_ref`、`evidence_bundle_ref`、`report_ref`、`failure_classification`、`created_at`、`updated_at`。阶段状态必须能区分 target failure、Oracle failure、provider failure、infrastructure failure 和 report/analysis failure。

**CLI 对齐**：CLI 可以同步完成一次 Run，但必须输出同样的 `EvaluationRun` 和 artifact refs；GUI 可以轮询 API，不应依赖 CLI 的 stdout 文本格式。

### 11.5 evaluation scope

**问题**：用户需要确认评测只读取哪些项目、运行哪些版本、使用哪个 fixture root、允许多少 Provider 成本以及是否允许外部副作用；当前这些信息散落在多个请求字段中，没有一份不可变的 scope contract。

**需要表达的能力**：

```json
{
  "source_access": "read_only",
  "baseline_version": "v1.0",
  "candidate_version": "v1.1",
  "fixture_root_ref": "fixture-root-001",
  "provider_binding_id": "binding-control-plane-001",
  "target_execution": ["baseline", "candidate", "combined"],
  "max_cost_usd": 0.03,
  "max_wall_time_seconds": 360,
  "external_side_effects": "denied",
  "operator_confirmed": true
}
```

该 scope 应在 EvaluationRequest 创建时冻结，并被 Run、Evidence Bundle、Report 和 Gate 引用。否则用户在页面上看到的“权限范围”只是临时文案，不能用于审计或复现。

## 12. Running 页面：从 Readiness 到完整 Run

### 12.1 页面目标

Running 页面回答：

1. 这次运行是否被允许开始？
2. 当前运行到了 Planning、Scenario Generation、Readiness、Execution、Analysis 的哪一步？
3. 每个场景/Arm 是否完成，有没有 Oracle evidence、trace、cost、latency？
4. 如果失败，失败发生在 Agent、Oracle、Provider 还是基础设施？

页面不显示一个没有来源的“正在运行”动画。每个状态都必须来自 `EvaluationRun`、`EvaluationReadinessResult` 或 Evidence Bundle。

### 12.2 进入 Running 的前置状态

进入 Running 前必须有：

- validated `EvaluationRequest`。
- immutable `EvaluationPlan`，且 `frozen=true` 的 scenario provenance 完整。
- `EvaluationReadinessResult.status=ready`。
- 已冻结的 `evaluation_scope`。
- 对 Skill Pair，预计矩阵为每个 scenario 的 `a_only`、`b_only`、`combined`。

已有接口：

```text
GET  /api/v1/projects/{project_id}/evaluations/plans/{plan_id}
POST /api/v1/projects/{project_id}/evaluations/readiness
```

Readiness 阻断时，页面提供：

- 每个 scenario 的 status。
- fixture_id、check name、detail。
- blocking reasons。
- “返回 New Evaluation 修复”与“重新检查”两个动作。

不得提供“忽略 Readiness 继续运行”按钮。

### 12.3 Running 页面信息区

#### A. Run Header

- Agent / project / evaluation name。
- Request ID、Plan ID、Run ID。
- baseline/candidate version。
- component type/name/members。
- scope 摘要：fixture、provider、budget、external side effects。
- 当前总状态和最后更新时间。

#### B. Pipeline

推荐阶段：

```text
Request validated
  -> Planning
  -> Scenario Generation
  -> Readiness
  -> Execution
  -> Oracle verification
  -> Evidence sealed
  -> Analysis / Report
  -> Release Gate
```

阶段状态至少支持 `pending`、`running`、`passed`、`blocked`、`failed`、`unresolved`。如果后端尚未产生某阶段状态，UI 显示 `not_started`，不能显示 `passed`。

#### C. Scenario Matrix

Skill Pair 的每一行是一个 scenario，每一列至少包括：

| 列 | 内容来源 |
| --- | --- |
| Scenario | category、scenario_id、scenario_hash |
| Prompt | frozen scenario 的 user_prompt；默认折叠 |
| A-only | condition status、oracle outcome、latency、cost |
| B-only | condition status、oracle outcome、latency、cost |
| Combined | condition status、oracle outcome、latency、cost |
| Evidence | evidence_refs、trace ref、oracle ref |

Skill Evaluation 与 Tool Regression 使用各自的 Experiment/Condition 列，但仍归一为 `EvaluationRun` 和 `Evidence Bundle`，不在 GUI 复制三套表格逻辑。

#### D. Run Metrics

从 `InteractionMatrixArtifact.metrics` 或对应 Evidence Bundle 读取：

- scenario count。
- expected/verified condition count。
- passed/failed/unresolved condition count。
- failure rate。
- total cost。
- total latency。
- token/model/tool call totals（如果 Artifact 提供）。

所有指标必须显示样本数和计算范围，不使用“显著提升”“最佳组合”等超出覆盖范围的文案。

### 12.4 Running 页面动作

| 动作 | 是否需要 API | 规则 |
| --- | --- | --- |
| Run Readiness | 已有 `POST /evaluations/readiness` | 只重新计算 readiness，不改变冻结 Plan |
| Start Evaluation | 需要 `POST .../runs` | 只允许 readiness=ready；生成新的 run_id |
| Refresh Status | 需要 `GET .../runs/{run_id}` | 只读取，不重新运行 |
| View Matrix Cell | 需要 matrix/evidence GET | 展示证据引用，默认不展开全部原始 trace |
| Retry | 需要新的 run endpoint | 只能创建新 Run，旧 Run 与证据不可覆盖 |
| Cancel/Pause | 当前没有对应 CLI 语义 | 在后端提供可恢复合同前不要显示 |
| Open Report | 需要 `report_ref` | 只有报告已生成且通过 schema validation 才可进入 Report |

### 12.5 CLI 对齐字段

GUI 的 Run 配置应能映射到 CLI `evaluation interaction-matrix`：

| CLI 参数 | GUI 信息 | 目标 API 字段 |
| --- | --- | --- |
| `--project-id` | 当前项目 | path parameter |
| `--plan` | frozen EvaluationPlan | `plan_id` |
| `--manifest` | target manifest reference | `target_manifest_ref` |
| `--cache-root` | runtime environment cache | `environment_cache_ref` |
| `--fixture-root` | scope 中的 fixture root | `fixture_root_ref` |
| `--run-root` | 后端分配的 run workspace | 不由用户任意指定；返回 `run_workspace_ref` |
| `--interaction-name` | 评测名称/interaction | `interaction_name` |
| `--evaluation-id` | Evaluation Request/Run identity | `evaluation_id` |
| `--oracle-command-part` | 不直接暴露 | registered oracle contract ref |
| `--oracle-id` | Oracle metadata | `oracle_id` |
| `--oracle-type/version` | Oracle metadata | `oracle_type` / `oracle_version` |
| `--binding` | target ProviderBinding | `target_provider_binding_id` |

## 13. Report 页面：报告、证据与 Release Gate

### 13.1 页面目标

Report 页面不是把 JSON 美化，而是让用户从产品问题走到可审计的 Release Decision：

```text
Product Summary
  -> Experiment Analysis
  -> Scenario Coverage / Stability
  -> Evidence Explorer
  -> Limitations
  -> Deterministic Release Decision
```

### 13.2 报告来源

Report 页面支持两个入口：

1. **从本次 Run 打开**：使用 `report_ref` 读取服务端持久化的 ProductEvaluationReport。
2. **导入已有报告**：兼容 CLI `report product/pair/tool` 输出的 JSON，再交给 API schema validation 和 Gate 校验。

当前 GUI 的本地 JSON 导入路径可以保留，但它是兼容路径，不应替代服务端 Report Store。导入的报告必须经过：

```text
parse JSON
  -> ProductEvaluationReport schema validation
  -> project_id / report_id / report_hash 校验
  -> deterministic Release Decision Gate
  -> local display
```

已有接口：

```text
POST /api/v1/projects/{project_id}/release-decision
POST /api/v1/products/{project_id}/evaluation-reports
POST /api/v1/products/{project_id}/reports/product
GET  /api/v1/products/{project_id}/evolution/reports/{report_id}
```

注意：`/api/v1/products/...` 是旧 Product/Evolution 体系，`/api/v1/projects/...` 是当前 Project Intelligence 主线。GUI 不应把两套报告模型混为同一个列表。

### 13.3 Report 页面信息层级

#### A. Product Summary

从 `product_overview`、`evaluation_context`、`executive_summary` 展示：

- 这是什么 Agent 能力。
- 本次用户任务与约束。
- 本次评测覆盖了哪些场景/实验。
- 在覆盖范围内观察到的能力贡献或能力损失。
- 产品建议与 follow-up。

文案必须保留当前覆盖范围，例如“在本次覆盖的 3 个 scenario 中观察到……”，禁止在 UI 中升级为“证明能力提升”。

#### B. Experiment Overview / Analysis

从 `experiment_overview`、`experiment_analysis` 展示：

- experiment purpose / comparison question。
- baseline/candidate 或 A-only/B-only/Combined 的差异。
- 每个结论对应的 `evidence_refs`。
- 未解决项与失败分类。

Pair Report 的 `interaction_analysis` 必须按照以下六个维度展示一次且只展示一次：

1. Trigger。
2. Capability Contribution。
3. Synergy Gain。
4. Coordination。
5. Conflict / Interference。
6. Reliability & Cost。

如果某维度没有足够证据，显示 `unresolved` 和原因，不由前端补写自然语言结论。

Skill Report 使用 trigger/execution/delivery/boundary 等实际 Plan dimensions；Tool Report 使用 tool calling success、argument correctness、downstream success、latency、cost 等实际维度。页面组件可以共用，但维度来自 Report，不写死 Pair 字段。

#### C. Scenario Stability

从 `scenario_stability` 展示：

- 场景总数与实际完成数。
- 每个场景 category、scenario hash、condition coverage。
- passed / failed / unresolved。
- 是否存在 coverage limitation。

不能只显示一个平均分遮住场景差异。

#### D. Evidence Explorer

至少支持三层：

1. **摘要层**：evidence status、artifact manifest hash、report hash、integrity status。
2. **条件层**：condition、oracle type/version、verdict/outcome、failure classification、latency/cost/token、evidence refs。
3. **原始引用层**：trace、tool call、oracle validation input、fixture materialization ref、matrix cell artifact ref；按需展开。

Evidence Explorer 只能读取 Evidence Bundle，不允许从 Analyst prose 反推事实。

#### E. Supplementary Benchmark Evidence

从 `supplementary_evidence` 展示：

- benchmark name/source ref。
- before/after comparable metrics。
- source SHA/integrity hash。
- evidence level=`external`。

它是辅助证据，不改变 AIG 主实验的 scenario coverage、Oracle verdict 或 Gate 规则。

#### F. Release Decision

从 `ReleaseDecisionGateResult` 展示：

- `decision`: `approve` / `review` / `block`。
- `deterministic=true`。
- ruleset。
- checks：每个 check 的 status、detail、evidence_refs。
- blocking reasons。
- review reasons。
- rationale。
- report hash 与 evidence manifest hash 的绑定关系。

`review` 不是失败，也不是自动批准；页面必须让用户看到需要人工判断的原因。`block` 必须给出可以定位到 Evidence/Integrity/Readiness 的原因。

### 13.4 Report 页面动作

| 动作 | 当前/目标接口 | 说明 |
| --- | --- | --- |
| Import Report JSON | 当前前端本地 parse | 兼容 CLI 输出；导入后必须 API validate + Gate |
| Validate Report | 当前 `POST /api/v1/products/{project_id}/evaluation-reports`；建议增加 Project 主线接口 | 只做 schema、project、hash、evidence binding 校验 |
| Run Release Gate | 当前 `POST /api/v1/projects/{project_id}/release-decision` | 只执行确定性代码，不调用 LLM |
| Read Report | 需要 `GET /api/v1/projects/{project_id}/reports/{report_id}` | 从 Run 或历史列表打开 |
| Read Evidence Bundle | 需要 `GET .../reports/{report_id}/evidence` | Evidence Explorer 数据源 |
| Read Matrix Artifact | 需要 `GET .../runs/{run_id}/matrix` | 场景/Arm/metrics 数据源 |
| Download JSON/Markdown/HTML | 需要 report projection/download API | 下载后 hash 必须与 report 一致 |
| Record Evaluation Knowledge | 目标 `POST /evaluation-knowledge` 或 `memory from-report` 对应 API | 必须由用户确认 component pattern，不能自动把 Analyst prose 当知识 |
| Start Follow-up Evaluation | 回到 New Evaluation 并带入 recommendation | 不直接修改当前 Report 或 Plan |

### 13.5 与 CLI Report/Release 命令对齐

| CLI | GUI | 共享对象 |
| --- | --- | --- |
| `agentguard report pair` | 从 Pair Run 生成/打开 Pair Report | `ProductEvaluationReport` |
| `agentguard report tool` | 从 Tool Run 生成/打开 Tool Report | `ProductEvaluationReport` |
| `agentguard report product` | 从 Skill/通用 artifact 生成/打开报告 | `ProductEvaluationReport` |
| `agentguard release gate --report` | Report 页面执行 Gate | `ReleaseDecisionGateResult` |
| `agentguard memory from-report` | 用户确认后写入 Evaluation Knowledge | `EvaluationKnowledge` |

如果 CLI 目前需要本地 artifact path，而 GUI 需要 API ref，应增加 artifact manifest/ref 适配层；两者保存的内容和 hash 必须一致，不能各自重新计算一套事实。

## 14. Running / Report 接口总矩阵

| 页面 | 用户动作 | 已有接口 | 仍需补齐的主线接口 | CLI 对等入口 |
| --- | --- | --- | --- | --- |
| Running | 读取冻结计划 | `GET /evaluations/plans/{plan_id}` | 无 | `evaluation plan --output` |
| Running | 重新做 Readiness | `POST /evaluations/readiness` | 无 | `evaluation readiness` |
| Running | 启动矩阵 | 无 | `POST /evaluations/{request_id}/runs` | `evaluation interaction-matrix` |
| Running | 轮询 Run | 无 | `GET .../runs/{run_id}` | CLI 同步输出 Run |
| Running | 读取每个 cell | 无 | `GET .../runs/{run_id}/matrix` | interaction artifact JSON |
| Running | 查看 evidence | 无 | `GET .../runs/{run_id}/evidence` | artifact/evidence refs |
| Running | 打开 Report | 无 | `GET .../runs/{run_id}/report` | `report pair/tool/product` |
| Report | 导入并验证报告 | 有旧 `/products/.../evaluation-reports` | `POST /projects/{project_id}/reports` | report commands 输出 JSON |
| Report | 运行 Gate | `POST /projects/{project_id}/release-decision` | 目标是支持 report_id/ref 输入并可读取历史 Gate | `release gate` |
| Report | 查看报告列表 | 无 | `GET /projects/{project_id}/reports` | output-dir 文件索引 |
| Report | 查看 Evidence Explorer | 无 | `GET /projects/{project_id}/reports/{report_id}/evidence` | evidence artifact |
| Report | 导出投影 | 无 | `GET /projects/{project_id}/reports/{report_id}/export?format=json|md|html` | report `--output-dir` |
| Report | 写入 Evaluation Knowledge | 只有 POST record | 增加 report-to-knowledge 的明确确认边界 | `memory from-report` |

## 15. 这轮不应提前做的事情

在上述接口契约和 CLI/API 等价测试没有确定前，不应：

- 先改 Running 的视觉动画，让页面看起来像正在执行。
- 让 Report 页面直接读取任意本地路径或自行解析 CLI stdout。
- 在前端复制 Interaction Matrix、Oracle 或 Gate 的计算逻辑。
- 为 Skill、Skill Pair、Tool 各自复制一套页面和状态机。
- 通过 `/api/v1/products/...` 的旧报告接口绕开 Project Intelligence 主线。
- 在 GUI 中开放任意 shell、Oracle command、cache root 或 run root 输入。

后续正确顺序是：先把共享接口模型和 CLI/API 等价测试补齐，再决定前端页面如何消费这些稳定对象。
## 16. Current implementation status (2026-08-06)

The earlier sections describe the pre-convergence gaps and design target. This section is the current verified status and supersedes those gap statements where they conflict.

### Implemented project-to-report path

| User action | Current backend contract | GUI status |
| --- | --- | --- |
| Load Project Overview | `GET /api/v1/projects/{project_id}/intelligence`, `/scans`, `/uploads`, `/provider-bindings`, `/evaluation-execution-configurations`, `/reports` | Connected |
| Upload and scan candidate | `POST /api/v1/projects/{project_id}/uploads`, then `POST /api/v1/projects/{project_id}/scan` with server-owned `upload://` source ref | Connected |
| Create evaluation request | `POST /api/v1/projects/{project_id}/evaluations` | Connected; snapshot/component validation is server-side |
| Generate plan | `POST /api/v1/projects/{project_id}/evaluations/plan` | Connected; dispatches through the strategy registry |
| Run readiness | `POST /api/v1/projects/{project_id}/evaluations/readiness` | Connected |
| Start and observe Run | `POST .../evaluations/{request_id}/runs`, `GET .../runs/{run_id}`, `GET .../events` | Connected |
| Read Matrix and Evidence | `GET .../matrix`, `GET .../evidence` | Connected |
| Generate and open report | `POST .../evaluations/runs/{run_id}/report`, `GET .../reports/{report_id}` | Connected |
| Report history/import/export/Gate | `GET /reports`, `POST /reports`, `GET /reports/{id}/export`, `POST /release-decision` | Connected |

The GUI only selects registered component/version/provider/configuration IDs. Manifest paths, cache roots, run roots, Oracle commands, and credentials remain server-owned.

### Strategy coverage

- `skill`: unified plan dispatch and real execution path through scenario/target/oracle/matrix/evidence/report/gate.
- `skill_pair`: existing real LLM scenario generation and A-only/B-only/combined interaction path is preserved behind the same plan/run/report contracts.
- `tool`: strategy dispatch is present and returns an explicit deferred/unsupported result. A full Tool Regression runner is intentionally outside this phase; the GUI must show the backend error rather than silently reuse Pair behavior.

### LightTable browser acceptance

Using the LightTable pair-nutrition project, Playwright verified: browser upload, candidate scan, New Evaluation, real plan generation with three LLM-generated scenarios, readiness, a completed 9-condition Run, Matrix/Evidence loading, persisted Product Evaluation Report, deterministic Gate, report history reopening, and JSON/Markdown/HTML export links. The final Gate result is `review` with `report_hash_valid=True`, complete Evidence, `oracle_verified=True`, zero failure rate, and a complete 3 x 3 interaction matrix. `review` is an intentional product-owner review result, not a transport or schema failure.

Provider/LLM invalid payloads and unsupported Tool planning remain visible `422` errors in the UI; no fixture or stale report is substituted.

### Remaining deliberate boundaries

- Full Tool Regression strategy/runner/oracle remains deferred.
- Evaluation Knowledge write-back from the GUI remains an explicit future confirmation flow; Analyst prose is not auto-promoted to ground truth.
- Visual redesign, Router adoption, and unrelated UI styling are not part of this alignment pass.
