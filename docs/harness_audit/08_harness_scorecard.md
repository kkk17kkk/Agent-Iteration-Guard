# Harness 评分卡

评分：0=缺失；1=模型/接口/计划；2=正常路径可运行；3=含失败、恢复和可复算证据。总分满分 54。

| # | 维度 | 分数 | 依据 |
| --- | --- | ---: | --- |
| 1 | Product baseline/versioning | 1 | Product/Version 可持久化，但无 stable baseline/import |
| 2 | ChangeSet correctness | 0 | 无 ChangeSet |
| 3 | Requirement-Capability-Test impact | 1 | 字段关系存在，不传播 |
| 4 | Change-aware selection | 0 | 固定全选 Case |
| 5 | Real Runner execution | 0 | 无 Runner |
| 6 | Typed artifact handoff | 2 | Handoff 真实写入，但尚非完整 artifact contract |
| 7 | Deterministic Oracle | 0 | 仅 `oracle_kind` schema |
| 8 | Evidence-Finding-Decision traceability | 1 | schema 存在，主链未使用 |
| 9 | LangGraph control state | 2 | 固定分支图真实运行 |
| 10 | Checkpoint and resume | 0 | checkpointer None |
| 11 | Idempotency and side-effect safety | 0 | 无 operation ID/policy |
| 12 | Controlled replan | 0 | 无 WorkItem/replan |
| 13 | Replay and ablation | 0 | 缺失 |
| 14 | Versioned memory/stale | 0 | 缺失 |
| 15 | Budget/terminal semantics | 1 | 有 pending/blocked，无 budget/reason taxonomy |
| 16 | Observability | 1 | SQLite Handoff 可看，未有事件/Trace |
| 17 | CLI end-to-end loop | 1 | CLI 到 pending，非验收闭环 |
| 18 | Mutation Benchmark/Meta-Eval | 0 | 缺失 |

**总分：10 / 54。**

## 硬性否决项

以下均成立，因此当前不得评为优秀 Harness：

1. 没有真实被测 Agent 执行。
2. 没有确定性 Oracle。
3. ReleaseDecision 不能从 Evidence 重算。
4. 没有 durable checkpoint 与恢复测试。
5. 外部副作用没有幂等机制。
6. ChangeSet 不能影响 EvalCase 选择。
7. 无固定输入、工具结果和环境的 Replay。
8. 没有完整 CLI 纵向场景。
9. Mutation Benchmark 没有程序化 Ground Truth。

“严重回归被 LLM Judge 自动放行”当前未发生，因为没有 LLM Judge；这不是能力得分。

## 形态判定

当前最接近：**数据模型脚手架 + 固定 LangGraph 流水线**，而非 Eval Workflow/Harness。其优点是没有假装 ready；缺点是尚无任何被测系统执行与验证证据。
