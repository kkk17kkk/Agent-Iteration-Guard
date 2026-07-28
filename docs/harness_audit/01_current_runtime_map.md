# 当前真实运行图

> 历史审计基线：`354c29e` 加 Phase 1.1 Harness 改动。P0 完成后，当前实际闭环和验证命令以根 README 与 `records/progress.md` 为准；本文件保留为 P0 实施前的缺口证据。

## 结论

当前系统是**带持久化领域模型的固定 LangGraph 工作流**，不是可执行的 Agent Iteration Harness。它可以登记产品、构造最小 Eval Case、生成一次带 Handoff 的 `awaiting_evidence` 或 `blocked` 记录；它不会导入版本、生成 ChangeSet、执行 Agent、运行 Oracle、写入 Evidence、恢复运行或依据证据做 Release Decision。

## 入口与调用链

| 表面 | 实际入口 | 调用关系 | 结果 |
| --- | --- | --- | --- |
| CLI | `backend/agentguard/cli.py:38` `main` | `report prepare` → `serialize_prepared_run` (`:34`) → `Service.prepare_harness_run` | 输出结构化 pending/blocked 记录 |
| Python 模块 | `backend/agentguard/__main__.py:1-4` | `python -m agentguard` → CLI `main` | 同上 |
| HTTP | `backend/agentguard/api.py:45` `prepare_report` | `Service(...).prepare_harness_run(...).as_dict()` | 返回同一数据形状 |
| 领域服务 | `backend/agentguard/service.py:87` | 读取 Product/EvalCase → 建 Run → 调 Graph → 建 Decision → `save_many` | 没有 Runner/Oracle 调用 |
| 图控制 | `backend/agentguard/harness.py:16-33` | `StateGraph` → `compile()` → `invoke()` (`:35-36`) | 进程内同步运行，未配置 checkpointer |
| 持久化 | `backend/agentguard/store.py:33-41` | 单 SQLite `records` 表 JSON payload | 只保存本次服务返回的对象 |

真实 `report prepare` 路径如下：

```text
CLI/API
  -> Service.prepare_harness_run
  -> Store.list("eval_case")
  -> HarnessRun(created)
  -> HarnessCoordinator.graph.invoke
       START -> intake -> plan -> (await_evidence | block) -> [hold_gate] -> END
  -> ReleaseDecision(pending | blocked)
  -> Store.save_many(run, handoffs, decision)
  -> JSON
```

`Service.fixture` 仅构造一个固定产品、Requirement、Capability 与 EvalCase（`service.py:54-85`）。它不调用被测 Agent。

## 重建出的 LangGraph

| 节点 | 实际输入/输出 | 实际控制作用 | 证据 |
| --- | --- | --- | --- |
| `intake` | `HarnessRun` → 一个 `evaluation_scope` Handoff | 不校验 manifest、版本或环境；只写说明文本 | `harness.py:39-55` |
| `plan` | Run → `planned` + 一个 `evaluation_plan` Handoff | 固定选择 `run.eval_case_ids` 全集 | `harness.py:58-75` |
| 条件边 | `eval_case_ids` 是否为空 | 非空到等待证据，空到阻断 | `harness.py:77-78` |
| `await_evidence` | Run → `awaiting_evidence` | 不执行 executor，只创建请求文本 | `harness.py:81-92` |
| `hold_gate` | Handoff → `release_hold` | 不读取 Evidence；仅写 pending 说明 | `harness.py:110-122` |
| `block` | Run → `blocked` | 空 Eval Case 时阻断 | `harness.py:95-107` |

图没有 backward edge、subgraph、并行节点、`Command`、`Send`、`interrupt`、retry policy、stream 或 durable checkpointer。`graph.compile()` 没有传入 checkpointer（`harness.py:33`）。

## 实际运行证据

在临时 SQLite DB 上创建 fixture 后连续两次调用 `Service.prepare_harness_run`，得到：

```json
{
  "first_status": "awaiting_evidence",
  "first_decision": "pending",
  "handoff_kinds": ["evaluation_scope", "evaluation_plan", "evidence_request", "release_hold"],
  "second_run_is_distinct": true,
  "checkpointer": "None",
  "evidence_records": 0
}
```

SQLite 记录类别为 Product/Version/Requirement/Capability/EvalCase、两个 HarnessRun、八个 Handoff 和两个 ReleaseDecision；没有 Evidence、Finding、Trace 或 Trial。命令和完整结果见 [06_failure_injection_matrix.md](06_failure_injection_matrix.md)。

## 错误、恢复与人工路径

- **错误路径**：只有 product 查找失败，CLI 返回退出码 2（`cli.py:59-70`），API 返回 404（`api.py:47-49`）。Runner、Oracle、环境和存储错误没有分类。
- **恢复路径**：不存在。Run 没有 `thread_id`、步骤号、checkpoint、operation ID 或 resume 入口；再次调用只创建另一个 Run。
- **人工路径**：不存在。没有 approval/interrupt，也没有人工 Gate。
- **前端**：只调用产品、fixture 和 report API，不拥有业务判断；当前审计不把 GUI 计入 Harness 能力。

## 库存

| 项目 | 结论 |
| --- | --- |
| CLI / FastAPI / SQLite / Docker / React | `implemented_and_exercised`（只覆盖登记与准备报告） |
| LangGraph 固定图 | `implemented_and_exercised` |
| Product、Version、Requirement、Capability、EvalCase | `implemented_and_exercised`（fixture/登记范围） |
| ComponentSnapshot、Evidence、Finding schema | `interface_only`；生产链无 producer/consumer |
| Runner、Provider、SSE、Trace、Oracle、Replay、Ablation | `absent` |
| Checkpoint、resume、approval、budget、任务事件 | `absent` |

## 运行图的关键风险

1. `await_evidence` 不是执行暂停点或可恢复 checkpoint，而是终止的内存图状态被转存为 JSON。
2. `Handoff.summary` 是当前协作的唯一实质内容；没有输入工件、所有者、验收条件或可消费输出。
3. `ReleaseDecision` 由 `Service` 在没有 Evidence 时直接生成（`service.py:97-110`），因此它不是由证据导出的结论。
