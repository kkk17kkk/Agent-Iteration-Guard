# 修复路线图

本路线图按纵向价值与可靠性依赖排序，不按模块数量排序。每一阶段完成前不启动下一阶段的横向扩展。

## P0：首个真实纵向闭环

**产品问题**：当前不能回答“某个 Skill/权限变更是否引入可验证回归”。

**系统根因**：核心工件之间没有 producer/consumer；Graph 只生成 Handoff，不调用 Runner/Oracle。

**涉及模块**：新增 `snapshot`、`changeset`、`router`、`runner`、`oracle`、`evidence`、`gate` 深模块；替换 `HarnessCoordinator` 的 pending-only 路径。当前 `Service.prepare_harness_run`（`backend/agentguard/service.py:87`）应成为创建 Run 的入口，而非直接产生最终 Decision。

**最小范围**：只实现 [04_first_vertical_slice.md](04_first_vertical_slice.md) 的 File Agent、两个版本、一个 Skill/permission change、三个 Case、一个 LocalFileRunner、一个路径 Oracle、一个 blocking Decision。

**不做**：LLM、第二 Runner、GUI、并发、Replay、Ablation、通用 registry。

**验收命令**：

```bash
agentguard run start --product-id <id> --baseline <stable> --candidate <candidate> --format json
```

**Definition of Done**：输出含 Snapshot/ChangeSet/EvalPlan/Trial/Trace/Verification/Evidence/Finding/Decision IDs；权限变更必然选择安全 Case，Oracle fail 后 Gate 为 `blocked`，且 Decision 可由保存的 Finding/Evidence 重算。

## P1：运行可靠性与安全边界

**产品问题**：真实执行不能在崩溃、重复提交或高风险工具下保持可信。

**系统根因**：没有 Event、checkpoint、operation id、budget、policy 或 failure taxonomy。

**数据模型**：`RunEvent`、`CheckpointRef`、`Operation`、`RunBudget`、`ToolPolicy`、`Approval`、`TerminalReason`。

**状态流**：`CREATED → SNAPSHOTTING → DIFFED → PLANNED → RUNNING → VERIFYING → DECIDED → RECORDED`；任何阶段可到 `WAITING_APPROVAL/BLOCKED/FAILED`。

**最小范围**：durable SQLite checkpointer；三类崩溃恢复；Runner/tool/oracle/environment/storage 的错误分类；默认拒绝高风险副作用；单 run budget。

**不做**：分布式调度、无限自动重试、多租户、真实高风险系统连接。

**验收**：03/04/05 中的 crash/resume、重复 operation、policy rejection、budget exhausted 测试均通过，工具调用不重复。

## P2：诊断闭环

**产品问题**：发现回归后无法稳定复现、比较或安全地提出归因。

**最小范围**：固定工具响应 ReplaySpec；一次单变量 permission/skill ablation；FailureTicket；Evidence stale propagation；受控 Replan（最多一次）。

**不做**：自动多轮根因、LLM 投票、广泛因果声明。

**验收**：同一 fixture replay 重现同一 Oracle fail；替换单一 policy 后产生 before/after Evidence delta；过期 Evidence 使 Decision 不再 ready。

## P3：系统自身证据

**产品问题**：Router 和诊断效果没有外部 ground truth。

**最小范围**：至少三个基础 Agent、程序化 mutation、项目/变异组合隐藏切分、全量回归对照、selection recall 和严重回归召回统计。

**不做**：调整统计口径、基于训练数据报告成功率、未审查的 LLM Ground Truth。

**验收**：每个指标可由保存样本/Trial 重算，报告样本数、切分与失败案例。

## P4：产品化扩展

在 P0-P3 均有证据前不启动：第二 Runner、真实 Provider、GUI 重构、SSE、更多 Agent profile 或外部集成。

## 开始下一阶段编码前的入口条件

1. 团队确认 P0 File Agent 场景与不可接受的权限回归定义。
2. 确认所有 P0 工具都在模拟本地工作区，无真实网络、删除、邮件、支付或部署副作用。
3. 确认 SQLite event/checkpoint 策略和工件 ID/checksum 合同。
4. 确认 Gate policy：critical deterministic Oracle failure 必须 `blocked`，LLM 不可覆盖。
5. 为 P0 选择一个明确的 stable/candidate fixture，而不是泛化 Agent 平台。
6. 先写 P0 验收与失败注入测试，再实施 Runner。

## 为什么不能靠 Prompt 或局部补丁

checkpoint、证据追溯、幂等、权限和终态都涉及跨节点、跨进程和外部副作用；它们需要稳定 ID、事务、状态机和可执行 Oracle。把这些判断塞进 Planner summary、LLM prompt 或在 `Service.prepare_harness_run` 后追加条件，会制造不可恢复的隐式状态，不能通过 P0 的崩溃与重算测试。
