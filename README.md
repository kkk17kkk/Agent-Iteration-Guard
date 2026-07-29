# Agent Iteration Guard

面向 Tool / Skill 型 Agent 的本地优先迭代验收 Harness。它不替代被测 Agent 的测试、Sandbox 或模型调用；它把版本、评测范围、协作交接、证据和发布门禁组织成可重放、可审查的工程闭环。

## 当前阶段与目标

当前为 **P4：Mutation Benchmark、批次恢复与受控外部 Runner**。P0 已证明确定性证据闭环，P1 将 LLM 限制为辅助工件，P2 将一个 File Management Agent 接入真实、受控且可恢复的本地 Runner，P3 验证多次执行的统计、复现和因果诊断；P4 在此基础上验证变异基准、批次恢复、缓存、并发和真实 LLM 成本账本：

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

## P1 LLM Assistant：解释和候选映射，不参与发布决策

P1 的调用发生在 P0 确定性闭环之后，且不回写 `EvalPlan`、`Finding` 或 `ReleaseDecision`：

```text
PathPolicyOracle -> Verification(failure_type=permission_violation)
                         -> Finding -> ReleaseDecision(blocked)
                                               |
                                               +-> LLM Assistant -> LLMAssistance(inferred)
```

`LLMAssistance` 会保存输入工件 ID、provider request ID、模型、prompt 版本和结果，并明确标记为 `inferred`，不是 Oracle Evidence。当前只有两个受限用途：

- `failure_explanation`：在 Oracle 已给出 `permission_violation` 后，解释可能关联的 ChangeSet；
- `requirement_mapping`：把 Requirement 与 ChangeSet 提示为候选 Capability 映射。

DeepSeek 配置写在仓库根目录 `.env`（不入库）；可从 `.env.example` 复制：

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-flash
```

生成失败解释：

```bash
python -m agentguard --db data/p1.db --format json assistant explain --run-id <harness_run_id>
```

生成需求映射候选：

```bash
python -m agentguard --db data/p1.db --format json assistant map \
  --product-id <product_id> \
  --requirement-id <requirement_id> \
  --changeset-id <changeset_id>
```

每次请求均为非流式 JSON、禁用 thinking、无 tools、`max_tokens=300`，且不重试。Provider 或输出契约失败会以可观察错误结束调用；系统不会伪造解释或改变已产生的 release 结论。

## P2 File Management Agent：真实 sandbox、恢复与幂等

P2 将第一个被测 Agent 从 fake trace 提升为真实的本地工具执行，但仍严格限制在 `TemporaryDirectory` sandbox 内：

```text
v2 cleanup instruction + delete_file capability
  -> read_file(README.md) -> write_file(README.md, "# XXX") -> delete_file(temporary.txt)
  -> ToolPolicy deny before deletion -> Trace -> Oracle -> Finding -> Failure Ticket -> blocked
```

每个 run 通过 SQLite `RunCheckpoint(next_step)` 保存下一安全节点；LangGraph 每次只执行一个 checkpoint 指定的节点。Runner 使用确定性 `operation_id(run, work_item, snapshot)`，并在真实工具 trace 完成后原子写入 `Operation(completed) + ExecutionResult`。因此“Runner 已完成、图尚未提交”后恢复会复用执行结果，不会重复工具调用；如果工具调用中途进程死亡，系统不会猜测性重试。

运行 P2 fixture：

```bash
python -m agentguard --db data/p2.db --format json fixture load file-management-agent
python -m agentguard --db data/p2.db --format json run start-file-management \
  --product-id <product_id> \
  --baseline <v1_version_id> \
  --candidate <v2_version_id>
python -m agentguard --db data/p2.db --format json run resume --run-id <harness_run_id>
```

输出包括真实 `tool_calls`、`operations`、所有 `checkpoints`、确定性 `verification`、`failure_tickets` 与 `release_decision`。`delete_file` 默认拒绝，真实删除、Shell、网络和工作区外路径均不开放。

## P3 多 Trial、固定环境 Replay 与单变量 Ablation

P3 在 P2 的本地 `TemporaryDirectory` Runner 上把每次评估显式保存为 `TrialSpec -> TrialResult`。每个 Trial 都有独立 `WorkItem`、真实工具 Trace、Oracle Verification 和 Evidence；汇总指标只从已持久化的 TrialResult 复算。

```text
Trial 1 (cleanup=false) -> read/write -> passed
Trial 2 (cleanup=false) -> read/write -> passed
Trial 3 (cleanup=true)  -> delete_file denied -> permission_violation -> failed
                                      |
                                      -> ReleaseDecision(blocked)
```

运行三次受控 Trial：

```bash
python -m agentguard --db data/p3.db --format json fixture load file-management-agent
python -m agentguard --db data/p3.db --format json run evaluate \
  --product-id <product_id> \
  --baseline <v1_version_id> \
  --candidate <v2_version_id> \
  --cleanup-attempts false,false,true
```

默认序列产生 `success_rate=2/3`、Bernoulli population `variance=2/9`、实际 `mean_latency_ms` 和 `total_cost_usd=0.0`。本地 Runner 没有外部计费，因此成本明确记为零，不做估算。

对失败 Trial 做固定环境 Replay 和单变量 Ablation：

```bash
python -m agentguard --db data/p3.db --format json run replay \
  --run-id <harness_run_id> \
  --source-trial-result-id <failed_trial_result_id>
python -m agentguard --db data/p3.db --format json run ablate-cleanup \
  --run-id <harness_run_id> \
  --source-trial-result-id <failed_trial_result_id>
```

Replay 在执行前校验候选快照、Tool Policy 和固定 Runner 环境指纹，并要求 Trace 指纹与 Oracle 结论同时复现。Ablation 只把 `cleanup_attempt` 从 `true` 改为 `false`，保存 `failed -> passed` 的 Evidence delta；版本、权限策略、环境、seed 和其他输入均保持不变。

这里的 `false,false,true` 是显式、受控的行为序列，用于验证非确定性评估的统计、复现和诊断机制；它不是对真实 LLM 随机性的性能宣称。真实模型或外部 Runner 后续可复用同一 Trial 接口，但必须额外记录其成本和环境事实。

## 验证

```bash
cd backend
python -m pytest -q
python -m compileall -q agentguard

cd ../frontend
npm run build
```

## P4 Mutation Benchmark：60 对、批次恢复与真实受控 Runner

P4 固定在 File Management Agent 纵向切片上生成 60 个有效的 baseline-candidate 版本对，均匀覆盖五种用户指定 mutation：`prompt`、`skill`、`tool_schema`、`permission`、`workflow`。每个版本对保存 Version、Snapshot、非空 ChangeSet 和程序化 Ground Truth；其中 30 对引入 cleanup 权限回归，30 对是安全对照。

```text
MutationPair -> ChangeSet -> EvalPlan(安全 case)
  -> BatchItem / checkpoint / cache
  -> Trial -> Local Tool Trace -> deterministic Oracle
  -> Evidence -> Finding -> ReleaseDecision
```

批次命令以受控 worker 波次运行每对至少三个 Trial。每个 `BatchItem` 在开始前预分配 HarnessRun ID；一个 item 或单个 Trial 完成后立即写入持久化状态。进程在边界崩溃后只继续缺失的 Trial，不会重复已完成的本地工具操作。相同 product、candidate 指纹、固定环境和 Trial 数量的后续批次会命中可观察的 cache。

```bash
python -m agentguard --db data/p4.db --format json benchmark create-file-management \
  --workers 2 --trials 3
python -m agentguard --db data/p4.db --format json benchmark run --batch-id <batch_id>
```

真实模型只承担一个受限的外部 Runner 决策：它只能输出 `{"cleanup_attempt": true|false}`，没有文件、Shell、网络或发布工具。随后仍由本地 `TemporaryDirectory` Runner 执行实际工具调用，ToolPolicy 与确定性 Oracle 决定是否存在权限回归，Release Gate 不读取模型的结论。每个模型调用都会保存 Inspect EvalLog 位置与输出哈希、输入/输出/缓存 token、逐类单价、总成本、价格来源和预算；外部调用中断不会被自动重发。

```bash
python -m agentguard --db data/p4-external.db --format json fixture load file-management-agent
python -m agentguard --db data/p4-external.db --format json run evaluate-external \
  --product-id <product_id> --baseline <v1_version_id> --candidate <v2_version_id> \
  --trials 3 --max-total-cost-usd 0.05
```

该命令读取仓库根目录 `.env` 的 `DEEPSEEK_API_KEY`，并把外部日志默认写入 `D:/codexdata/agentguard-inspect-logs`。P4 命令层硬性限制单次 smoke 预算为 `$0.05` 及以下；请求前按固定 prompt 与 `max_tokens=96` 做保守上界检查，请求后按保存的 token 和官方价格重新计算。外部 Runner、Provider 或输出契约失败时，Run 会被显式记为 `failed`，ReleaseDecision 保持 `pending`，绝不伪装成 Agent 权限回归或自动放行。

## 当前边界

P0 已支持显式 File Agent Manifest、版本 Snapshot、结构化 ChangeSet、规则 EvalPlan、WorkItem、确定性 fake Runner、路径 Oracle、Evidence-Finding-Decision 链和阻断 Gate。P1 新增真实 LLM API 的解释/候选映射。P2 新增一个受控的真实本地 File Management Agent、durable checkpoint、幂等 operation 和 Failure Ticket。P3 新增受控多 Trial、稳定性统计、固定环境 Replay 与单变量 Ablation。P4 新增 60 对程序化 mutation、批次级 checkpoint/resume、缓存、受控并发、Inspect 外部 Runner、可重算 token/cost 账本和真实模型 smoke。当前仍不支持复杂 Coding Agent、真实外部副作用、分布式调度，以及 PRD 所要求第六类 mutation 的完整覆盖；后者尚未纳入用户指定的本阶段五类 mutation 范围。
