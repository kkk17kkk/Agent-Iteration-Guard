# 失败注入矩阵与实际结果

审计命令在 `D:\codexdata\agentguard-venv` 与临时 SQLite DB 上运行：

```powershell
python -m pytest -q
python - < audit_runtime_probe.py
```

实际结果：`5 passed`；一次 fixture Run 为 `awaiting_evidence/pending`，连续第二次准备生成不同 run ID；`graph.checkpointer is None`；数据库 `Evidence` 数量为 0。以下“不可注入”不是预期结果，而是对当前代码无法产生该机制的实际边界记录。

| # | 注入/探针 | 实际结果 | 结论 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | 正常 fixture 评测 | 仅生成 pending Run，未执行 Agent | 非端到端验收 | `service.py:54-110`；运行探针 |
| 2 | Snapshot 后崩溃 | 无 Snapshot producer 或状态 | 不可注入 | `ComponentSnapshot` 仅 schema，`domain.py:41-48` |
| 3 | 并行 Trial 一成功一超时 | 无 Trial/并行节点 | 不可注入 | `harness.py:16-33` |
| 4 | Runner 完成、结果写入前崩溃 | 无 Runner/ExecutionResult | 不可注入 | 源码 inventory 无 Runner |
| 5 | 重复 resume | 无 resume 命令/checkpoint | 不可注入 | `cli.py:10-35`；checkpointer `None` |
| 6 | 重复 operation ID | 无 operation ID 或幂等表 | 不可注入 | `domain.py` 无该字段 |
| 7 | Tool Policy 拒绝危险调用 | 无 Tool Policy/工具调用 | 不可注入 | 源码 inventory 无 policy |
| 8 | Oracle 检测权限回归 | `oracle_kind` 未被读取 | 不可注入 | `domain.py:65-70`；`service.py:87-110` |
| 9 | Oracle 自身异常 | 无 Oracle 执行入口 | 不可注入 | 同上 |
| 10 | Runner 环境失败 | 无 Runner/环境模型 | 不可注入 | 源码 inventory |
| 11 | Budget exhausted | 无 Budget/terminal reason | 不可注入 | `HarnessRun` 字段见 `domain.py:73-82` |
| 12 | 无新增 WorkItem 的 Replan | 无 WorkItem/Replan 节点 | 不可注入 | `harness.py:16-33` |
| 13 | Evidence stale propagation | 运行时 Evidence 为 0 | 不可注入 | 运行探针 |
| 14 | ReleaseDecision 重算 | Decision 无 Finding/Evidence refs | 不可注入 | `domain.py:113-121` |
| 15 | 固定工具响应 Replay | 无 ReplaySpec 或 Runner | 不可注入 | 源码 inventory |
| 16 | CLI 中断后恢复 | CLI 无 run/resume 子命令 | 不可注入 | `cli.py:10-35` |
| 17 | 两个并发 Run 隔离 | Store 可保存不同 ID，但无并发调度/锁/隔离语义 | 未验证 | `store.py:33-41` |

## 已执行的特征探针

1. **两次同一请求**：`second_run_is_distinct=true`，说明当前行为是创建新 Run，不是 resume、idempotent submit 或 replay。
2. **Evidence 消费链**：`evidence_records=0`，同时 ReleaseDecision 为 pending；Evidence schema 不在主路径。
3. **持久化内容**：两次请求仅产生 2 个 HarnessRun、8 个 Handoff、2 个 ReleaseDecision，未产生 Trial/Trace/Finding。
4. **Graph durability**：`checkpointer=None`；Graph 结束后只依靠服务层显式 JSON save，不存在 LangGraph checkpoint 恢复。

这些结果不能支持“崩溃可继续”“证据可重算”“独立验证”或“安全执行”声明。P0 实现后，本表应替换为逐项命令、实际输出、DB event/checkpoint、terminal reason 与副作用计数。
