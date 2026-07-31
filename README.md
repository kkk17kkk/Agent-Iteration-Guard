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

## Ticket Agent：跨领域核心抽象验证

Ticket Agent 是第二个隔离的确定性 runtime，不是 File Agent 的条件分支。它复用同一套 durable checkpoint、`EvalPlan -> WorkItem -> ExecutionResult -> Verification -> Evidence -> Finding -> ReleaseDecision` 合同；File 与 Ticket 各自只提供 Runtime Adapter 的计划、执行、Oracle 和 Failure Ticket 文案。

可测试的 Ticket 回归包括：重复创建、非法关闭、未授权指派、丢失评论、错误 owner、缺失状态跳转、retry 造成的重复 comment 副作用，以及 workflow 跳过审批。Oracle 从保存的 Ticket 状态快照和规范化工具 Trace 得出结论；模型/Agent 不提交 failure 标签。

```bash
python -m agentguard --db data/ticket.db --format json fixture load ticket-agent
python -m agentguard --db data/ticket.db --format json run start-ticket \
  --product-id <product_id> --baseline <ticket_v1> --candidate <ticket_v2> \
  --case ticket_retry_duplicate_side_effect
```

Ticket 适配不向 Router、Evidence 或 ReleaseDecision 引入 `agent_type` 条件。它目前用于验证核心抽象与生命周期安全，不是 τ³-bench 或真实 LLM 能力声明。

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

## Stage 5：受控、事件驱动 Replan

Stage 5 不提供开放式 Planner。一次 `run replan` 只读取已持久化的 Harness 事实，按固定的安全优先级处理**一个**尚未处理的事件，并持久化 `ReplanRecord`：`trigger`、变更前/后 `EvalPlan`、新增 `WorkItem`、来源工件、预算前/后、风险升级与终止原因。它不会自行递归调用，也不会调用 LLM 生成计划。

| 事件 | 受控动作 | 终止边界 |
| --- | --- | --- |
| Trace 不完整 | 增加并执行 instrumentation trial | `applied` |
| 结果不稳定 | 在保存的追加 Trial 预算内增加一个 trial | `applied` / `budget_exhausted` |
| 权限回归 | 注册并执行额外 safety `EvalCase` | `applied`，风险升级 |
| Runner 环境故障 | 默认标记 run `blocked`；仅显式授权才切本地隔离 Runner | `runner_blocked` / `applied` |
| Replay 不可复现 | 保存 environment capture，run 转为 `awaiting_evidence` | `unresolved` |

```bash
python -m agentguard --db data/p3.db --format json run replan \
  --run-id <harness_run_id> --additional-trial-budget 1

# 只有调用方明确允许时，环境故障才切换到本地隔离 Runner。
python -m agentguard --db data/p3.db --format json run replan \
  --run-id <harness_run_id> --allow-runner-switch
```

对应 HTTP 入口为 `POST /api/v1/runs/{harness_run_id}/replan`，body 可含 `additional_trial_budget` 与 `allow_runner_switch`。Replan 的 `unresolved` 证据不能成为 Release Gate 的通过依据。

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

## Stage 2：真实 LLM Action Loop 验收

Stage 2 将真实 DeepSeek 调用限定为 `AgentAction -> ToolPolicy -> local sandbox -> Observation -> AgentAction` 闭环。HTTP / JSON 脚本 Agent 仅验证 Adapter 契约，不能作为真实 LLM 验收证据。

真实模型 Gate 必须同时拥有：逐步 DeepSeek request/model/输入输出哈希证据、同一 `ensure_title` 任务在“需修改”和“已满足”两种 observation 下的不同后续 Action，以及被独立 ToolPolicy/Oracle 阻断的未授权删除。模型自报的失败标签和额外字段会被拒绝；无效 Provider 响应会显式记录为 `model_invalid_response`，不会伪装成 Agent 回归。

在已持久化 Stage 1 `PASS` 的数据库中运行最小验收：

```bash
python -m agentguard --db D:/codexdata/agentguard-stage2-real.db --format json stage2 run \
  --batch-id <stage1_pass_batch_id> --task ensure_title --fixture-variant needs_update --model real_llm --max-steps 4
python -m agentguard --db D:/codexdata/agentguard-stage2-real.db --format json stage2 run \
  --batch-id <stage1_pass_batch_id> --task ensure_title --fixture-variant already_satisfied --model real_llm --max-steps 4
python -m agentguard --db D:/codexdata/agentguard-stage2-real.db --format json stage2 run \
  --batch-id <stage1_pass_batch_id> --task cleanup --model real_llm --max-steps 3
python -m agentguard --db D:/codexdata/agentguard-stage2-real.db --format json stage2 gate \
  --batch-id <stage1_pass_batch_id> --artifacts-root D:/codexdata/agentguard-stage2-real-artifacts
```

密钥从仓库根目录 `.env` 加载；每个请求使用固定 `temperature=0`、`max_tokens=300`，并受每个 run 的 `--max-steps` 限制。该微型矩阵只能证明真实模型接入、observation 分支和权限回归检测，不代表通用 Agent 能力或模型性能。

### Provider 原生 Tool Runtime 与批次预算

`stage2 runtime-batch` 不再要求模型返回 Harness 私有 `AgentAction` JSON。它使用 DeepSeek 原生 function calling：Provider 返回 `tool_call`，Harness 执行本地受控工具，把标准 `tool` 消息和新的 observation 放回会话，再请求下一步。每一轮保存 Provider request ID、native tool-call ID、请求/响应哈希、规范化 Action、工具 Trace 与 API `usage`。

```bash
python -m agentguard --db D:/codexdata/agentguard-stage2-native.db --format json stage2 runtime-batch \
  --batch-id <stage1_pass_batch_id> --budget-usd 0.01 --max-steps 6 \
  --artifacts-root D:/codexdata/agentguard-stage2-native-artifacts
```

Batch 在发送每个请求前按固定 `max_tokens=192` 和输入字节上界检查剩余额度，随后根据 Provider `usage` 重算账本。Budget Gate 只有在原生 tool trace、成功编辑、权限阻断和实际成本不超过预算同时成立时才为 `PASS`。预算耗尽和 Provider/契约错误会保留为运行时状态，不会被归因成被测 Agent 回归。当前价格表固定为 `deepseek-v4-flash`；其他模型显式拒绝，避免用错误单价形成账本。

### Retry / Idempotency Runtime Corpus（第六类 mutation）

第六类 mutation 是 `retry_idempotency`，但它不加入静态 Manifest 变异器后再用预设标签充数。它在持久化 Stage 2 sandbox 中实际注入“文件副作用已发生、Action 尚未提交”的崩溃边界，并只改变恢复策略：

- `stable_operation_id` 复用 pending Action identity，恢复时不重复副作用；
- `regenerate_operation_id` 刻意重生 identity，重新提交同一个写入；Harness 从真实 sandbox、`Stage2Operation`、checkpoint 和 Trace 计数检测重复副作用；
- 每个模式至少三次 Trial；候选必须被独立 Oracle 分类为 `retry_idempotency_violation` 并阻断 release；
- 固定的已记录 Action Trace 在新 sandbox 上 Replay；Ablation 仅将 identity 策略改回 stable，其他 Action Trace、任务和崩溃边界不变。

```bash
python -m agentguard --db D:/codexdata/agentguard-stage2-reliability.db --format json stage2 reliability-corpus \
  --batch-id <stage1_pass_batch_id> --model deterministic --trials 3 \
  --artifacts-root D:/codexdata/agentguard-stage2-reliability-artifacts

# Real DeepSeek tool-action generation, with a separate batch ledger.
python -m agentguard --db D:/codexdata/agentguard-stage2-reliability-real.db --format json stage2 reliability-corpus \
  --batch-id <stage1_pass_batch_id> --model deepseek_tools --trials 3 --budget-usd 0.02 \
  --artifacts-root D:/codexdata/agentguard-stage2-reliability-real-artifacts
```

`deterministic` 输出只证明 runtime recovery 的安全边界，报告会固定标记为 `PASS_WITH_LIMITATIONS`，绝不冒充真实 LLM 语义证据。真实模型 batch 若偏离首读、复核或 finish 协议，稳定对照也会被 Oracle 阻断；此时 Gate 保持 `BLOCKED`，而不是借助记录的 Replay Trace 或 mutation 标签伪造通过。

## 验证

```bash
cd backend
python -m pytest -q
python -m compileall -q agentguard

cd ../frontend
npm run build
```

## P4 Mutation Benchmark：60 对、批次恢复与真实受控 Runner

P4 固定在 File Management Agent 纵向切片上生成 60 个有效的 baseline-candidate 版本对，覆盖五种静态 Manifest mutation：`prompt`、`skill`、`tool_schema`、`permission`、`workflow`。第六类 `retry_idempotency` 由上述独立 runtime corpus 覆盖，因为其 Ground Truth 必须是实际 checkpoint/side-effect 行为，不能由静态版本对的预设 release 标签替代。每个静态版本对保存 Version、Snapshot、非空 ChangeSet 和程序化 Ground Truth；其中 30 对引入 cleanup 权限回归，30 对是安全对照。

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

P0 已支持显式 File Agent Manifest、版本 Snapshot、结构化 ChangeSet、规则 EvalPlan、WorkItem、确定性 fake Runner、路径 Oracle、Evidence-Finding-Decision 链和阻断 Gate。P1 新增真实 LLM API 的解释/候选映射。P2 新增受控的本地 File Management Agent 与 Ticket Agent，两者通过 Runtime Adapter 共享 durable checkpoint、幂等 operation、Evidence 和 Failure Ticket 链。P3 新增受控多 Trial、稳定性统计、固定环境 Replay 与单变量 Ablation。P4 新增 60 对程序化 mutation、批次级 checkpoint/resume、缓存、受控并发、Inspect 外部 Runner、可重算 token/cost 账本和真实模型 smoke。Stage 2 另有第六类 `retry_idempotency` 的真实持久化 runtime corpus、三 Trial、Replay/Ablation 和独立 budget ledger。当前仍不支持复杂 Coding Agent、真实外部副作用、分布式调度，也尚未达到 PRD 所需三类 benchmark Agent、60–100 个跨类有效版本对和隐藏切分的最终验收。
