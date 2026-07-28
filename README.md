# Agent Iteration Guard

面向 Tool / Skill 型 Agent 的本地优先迭代验收 Harness。它不替代被测 Agent 的测试、Sandbox 或模型调用；它把版本、评测范围、协作交接、证据和发布门禁组织成可重放、可审查的工程闭环。

## 当前阶段与目标

当前为 **Phase 1：协作型 Harness 基础**。本阶段的唯一目标是建立可靠的运行账本，而不是伪造“已评测”的 demo：

- 建立产品、初始版本、需求、能力和 Eval Case 的结构化登记；
- 用 LangGraph 编排显式角色交接：`intake → planner → executor → verifier → gatekeeper`；
- 每次准备评测都会保存 `HarnessRun`、不可变的 Handoff 记录和 Release Decision；
- 没有 Eval Case 时阻断；有 Case 但没有 Runner 证据时保持 `awaiting_evidence / pending`，绝不自动放行。

后续目标按 PRD 推进：

1. **Phase 2**：组件扫描、Manifest、指纹、ChangeSet 与按风险的测试选择；
2. **Phase 3**：Runner Adapter、Trial、Trace 归一化、证据校验、Replay / Ablation 与版本记忆；
3. **Phase 4**：Mutation Benchmark、回归与选择指标、Release Gate 和可复算报告。

## 运行模型

```text
CLI / HTTP API
       │
       ▼
Application Service
       │
       ├── SQLite Store：产品、版本、运行、交接、证据、决策
       └── Harness Coordinator（LangGraph）
              intake → planner → [await evidence | block] → gatekeeper
```

领域模型和 SQLite 不依赖 LangGraph、CLI 或前端。LangGraph 仅负责协调状态转移；未来的 Runner、Judge 或真实 Agent 只能通过明确输入输出把 Evidence 交给 Harness，不能直接篡改 Release Decision。

### Phase 1 状态语义

- `planned`：已确认版本与评测范围；
- `awaiting_evidence`：已生成计划，等待执行器和 Oracle 提供可验证证据；
- `blocked`：没有可执行评测范围，不能给出发布结论；
- `pending`：发布结论仍等待验证证据；`ready` 不是本阶段可产生的状态。

## 快速开始

后端要求 Python 3.11+：

```bash
cd backend
python -m pip install -e ".[dev]"
agentguard init
agentguard fixture load minimal
agentguard product list
```

准备一次可审计的 Harness Run：

```bash
agentguard report prepare --product-id <product_id> --format json
```

JSON 输出包含 `harness_run`、每次角色交接的 `handoffs` 与 `release_decision`。无效产品返回退出码 `2`，并携带 `stage`、`reason`、`next_step`。

启动 API：

```bash
cd backend
uvicorn agentguard.api:app --reload --port 8000
```

启动前端工作台：

```bash
cd frontend
npm install
npm run dev
```

## P0 File Agent 闭环

P0 使用仓库内的两个确定性 File Agent Manifest：v2 同时改变 Skill、扩展工具能力，并请求向 `secrets/leak.txt` 写入。Harness 必须将权限变化映射到安全测试，再由路径 Oracle 阻断发布。

```bash
cd backend
python -m agentguard --db data/p0.db --format json fixture load file-agent
python -m agentguard --db data/p0.db --format json run start \
  --product-id <product_id> \
  --baseline <v1_version_id> \
  --candidate <v2_version_id>
```

输出中可依次追溯：`changeset`（`permission_changed`、`tool_capability_expanded`）→ `eval_plan`（安全 Case 被选中）→ `work_items` → `executions` → `verifications` → `evidence` → `findings` → `release_decision=blocked`。`events` 给出 LangGraph 的 `RUN_CREATED → PLAN_CREATED → TRIALS_COMPLETED → VERIFICATION_COMPLETED → FINDING_CREATED → RELEASE_DECIDED → RUN_RECORDED` 状态流。

也可以显式导入本地版本：

```bash
python -m agentguard --db data/p0.db version import \
  --product-id <product_id> \
  --source fixtures/file_agent/v1 --label v1
```

## 验证

```bash
cd backend
python -m pytest -q
python -m compileall -q agentguard

cd ../frontend
npm run build
```

## 当前边界

P0 已支持显式 File Agent Manifest、版本 Snapshot、结构化 ChangeSet、规则 EvalPlan、WorkItem、确定性 fake Runner、路径 Oracle、Evidence-Finding-Decision 链和阻断 Gate。它仍不支持 checkpoint/resume、真实 Provider、网络或外部副作用、并行 Trial、Replay/Ablation、变异基准或 LLM 裁决；这些能力不能由 P0 的模拟执行结果替代。
