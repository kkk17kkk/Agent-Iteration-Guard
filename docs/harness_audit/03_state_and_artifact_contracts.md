# 状态与工件合同

## 当前 schema 的审计

`Handoff`、`Evidence`、`Finding` 和 `ReleaseDecision` 已有名字，但不能构成证据链：

| 当前对象 | 现状 | 问题 |
| --- | --- | --- |
| `HarnessRun` (`domain.py:73-82`) | 产品、版本、Case IDs、状态 | 无 `thread_id`、operation、预算、终态原因、环境或版本快照引用 |
| `Handoff` (`domain.py:85-93`) | 角色、kind、summary、Case/Evidence IDs | `summary` 承载主要语义；无 owner、输入工件、预期输出或验收标准 |
| `Evidence` (`domain.py:95-103`) | source/level/summary | 无 producer、原始结果、trace、oracle、checksum 或环境；当前未被写入 |
| `Finding` (`domain.py:105-111`) | 标题与 evidence level | 无 Evidence IDs、严重度、状态、归因或失效条件 |
| `ReleaseDecision` (`domain.py:113-121`) | 状态与 rationale | 无 Finding/Evidence IDs；不能重算 |
| SQLite records | 仅 `kind/id/product_id/payload` | 无不可变事件、工件版本、事务边界标记或索引化 lineage |

因此当前 Handoff 是**文本包装的固定节点痕迹**，不是可消费的协作协议。

## 推荐控制状态

LangGraph State 只保存控制面和工件引用，不保存 Trace 正文：

```python
class RunControlState(TypedDict):
    run_id: str
    thread_id: str
    current_work_item_id: str | None
    pending_work_item_ids: list[str]
    completed_work_item_ids: list[str]
    artifact_refs: list[str]
    budget: BudgetState
    approval: ApprovalState
    terminal_reason: str | None
```

`thread_id == run_id`。每次节点提交前先落业务工件和 `RunEvent`；图 checkpoint 只保存下一步的控制状态和同一 `run_id` 的引用。

## P0 工件协议

所有工件共享：`artifact_id`、`schema_version`、`producer`、`input_artifact_ids`、`product_id`、`source_version_id`、`environment_ref`、`created_at`、`checksum`、`status`、`stale_at`。自由文本只能放 `explanation`。

| 工件 | P0 必需字段与 producer | 消费者 |
| --- | --- | --- |
| `ComponentSnapshot` | 文件清单、hash、component type；Snapshot service | Change analyzer |
| `ChangeSet` | baseline/candidate snapshot IDs、每项 component change、risk；Analyzer | Router |
| `ImpactAssessment` | change ID、Requirement/Capability/Case IDs、规则理由；Router | EvalPlan |
| `WorkItem` | owner、目标、输入 refs、期望输出、准入条件、budget、allowed tools、状态；Planner | Runner/Oracle/Gate |
| `EvalPlan` | selected/skipped case、原因、risk、oracle、runner、trials、escalation；Router | Trial subgraph |
| `TrialSpec` | work item、输入 fixture、tool policy、operation ID、seed；Scheduler | Runner |
| `ExecutionResult` | operation ID、runner status、原始结果 ref、failure class；Runner | Trace normalizer |
| `NormalizedTrace` | tool calls、policy decisions、环境、时间；Normalizer | Oracle |
| `VerificationResult` | oracle、expected、observed、passed、severity、failure class；Oracle | Evidence builder |
| `EvalEvidence` | Verification/Trace/Result refs、level、checksum；Evidence builder | Finding/Gate |
| `Finding` | Evidence IDs、严重度、影响、状态；Finding service | Gate/Replan |
| `ReleaseDecision` | Finding IDs、policy version、结论、terminal reason；Gate | CLI/report |

## WorkItem 所有权

```text
Gatekeeper: 唯一可写 ReleaseDecision 的 owner
Router/Replan controller: 只能创建或替换未开始的 WorkItem
Runner: 只能写 ExecutionResult/Trace；不能判定 release
Oracle: 只能写 VerificationResult；不得调用 LLM 覆盖确定性结果
LLM analyzer（后置）: 只能产生 explanation 或 root-cause candidate
```

P0 中可以没有 LLM。多 Agent 不是前提；只有出现独立上下文、独立工具权限和可验证工件交接时，角色才值得实现为 Agent。
