# 关键机制设计

以下为实施设计，不是当前实现声明。核心原则：业务事实存 SQLite 工件/事件，LangGraph 只推进可恢复控制状态。

## 1. RunEvent、checkpoint 与 resume

`RunEvent(run_id, sequence, event_type, work_item_id, operation_id, input_hash, artifact_ids, occurred_at)` 追加写入，`sequence` 在同一 Run 内唯一。事件类型至少包括 `RUN_CREATED`、`SNAPSHOT_COMPLETED`、`CHANGESET_CREATED`、`PLAN_CREATED`、`TRIAL_STARTED`、`TRIAL_COMPLETED`、`VERIFICATION_COMPLETED`、`FINDING_CREATED`、`RELEASE_DECIDED`、`RUN_BLOCKED`、`RUN_FAILED`、`RUN_RECORDED`。

- `thread_id = run_id`；每个成功边界调用 durable SQLite checkpointer。
- 节点先在业务事务中写工件和 event，再提交 graph checkpoint；resume 根据最后一个已提交 event 计算下一安全节点。
- `operation_id = hash(run_id, work_item_id, attempt, input_hash)`；Runner 在执行前登记，执行后原子写 Result。恢复遇到已完成 operation 时复用结果，不重放副作用。
- `resume` 继续同一 Run 未完成工作；`restart` 创建新 Run；`replay` 以固定输入/环境重建旧 Trial；`retry` 只针对允许且幂等的瞬时失败。
- 可恢复：SNAPSHOTTING、DIFFED、PLANNED、RUNNING、VERIFYING、DECIDED。不可恢复：`unrecoverable_storage_error`；已执行但无结果的非幂等操作进入人工门禁。

MVP 测试：Trial 前崩溃、执行完成但未写图状态、Finding 已写但 Gate 未写。每个测试断言事件序列、工具调用次数和最终工件 checksum。

## 2. Evidence → Finding → Decision

`EvalEvidence` 只能由 Evidence builder 创建，输入为 `ExecutionResult`、`NormalizedTrace`、`VerificationResult` 的 IDs。`Finding` 必须列出 `evidence_ids`；`ReleaseDecision` 必须列出 `finding_ids` 和 policy version。Gate 用纯函数 `decide(findings, policy)` 重新计算，禁止接受自然语言 rationale 作为唯一输入。

```text
ExecutionResult + NormalizedTrace + VerificationResult
  -> EvalEvidence(verified/supported/inferred/unresolved)
  -> Finding(severity, status)
  -> ReleaseDecision(policy, finding_ids, status)
```

低于 `verified` 的安全结论不能使 Decision 为 `ready`。Evidence 的 source version、环境或输入 checksum 变化时，标记 stale，并向 Finding/Decision 传播。

## 3. WorkItem 与受控协作

```text
work_item_id, owner, objective, input_artifact_ids,
expected_output_type, acceptance_criteria, budget, allowed_tools,
status, handoff_condition, blocked_reason, operation_id
```

所有者由确定性状态机指定：Router 拥有 plan，Runner 拥有 execution，Oracle 拥有 verification，Gatekeeper 拥有 release。Replan Controller 只能更改未开始 WorkItem。LLM 若接入，只产生候选解释，不能成为 WorkItem owner、Evidence producer 或 Gate 决策者。

## 4. Change-aware Router 与 Replan

Router 输入 `ChangeSet + component criticality + graph mapping + historical failures + side-effect risk + uncertainty`，输出每个 Case 的 selected/skipped、理由、risk、oracle、runner、trial count 和 escalation rule。规则应是可测普通代码：

- permission/tool schema 改变：安全 Case 与全量 smoke 不可跳过；
- high/critical：相关模块测试 + 产品回归 + 人工门禁；
- uncertainty 或缺映射：升级范围，不能降级；
- 低风险：相关 Case + smoke。

只有前提失效、runner 不支持、权限拒绝、critical finding、Trace 缺失、环境故障、稳定性阈值失败、预算耗尽能触发 Replan。记录 trigger、原计划、新旧 WorkItem diff；最多 `max_replans` 次，超过后 `unresolved`/`blocked`。

## 5. Runner、Tool Policy、审批

`RunnerAdapter.execute(TrialSpec) -> ExecutionResult` 必须接收显式 `ToolPolicy` 和 sandbox ref。Policy 按只读扫描、工作区写入、命令、网络、不可逆副作用分级；P0 默认拒绝高风险项。每次 tool call 记录：`operation_id/run_id/trial_id/tool_name/arguments_hash/policy_decision/approval_id/sandbox_id/time/result_ref/side_effect_class`。

需要用户批准、低置信 Gate、Oracle 冲突或预算扩张时，Graph 用 interrupt 进入 `WAITING_APPROVAL`。Runner 故障、Tool fixture 故障、Oracle 故障、环境故障和 Agent 失败必须各自编码，禁止统一为 Agent regression。

## 6. Oracle、Replay 与预算

Oracle 是纯确定性函数，输出 `VerificationResult(oracle_id, target_artifact_id, expected, observed, passed, severity, evidence_ids, failure_class)`。P0 采用路径/工具调用序列 Oracle。

`ReplaySpec` 固定 source run/trial、版本、输入 fixture、工具 fixture refs、memory/environment snapshot、seed、model config 与 budget。Replay 不等同 rerun；Ablation 一次只替换一个组件，输出 before/after Evidence delta。

`RunBudget`：`max_steps/max_trials/max_wall_time/max_cost/max_tokens/max_retries/max_replans/max_replays/max_ablations`。所有终态写 `terminal_reason`，包括 `acceptance_satisfied`、`critical_regression`、`low_confidence`、`human_review_required`、`budget_exhausted`、`runner_failure`、`environment_failure`、`oracle_failure`、`stuck`、`cancelled`、`unrecoverable_storage_error`。
