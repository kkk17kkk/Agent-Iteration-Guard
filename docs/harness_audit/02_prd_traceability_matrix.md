# PRD 可追踪矩阵

> 本矩阵为 P0 实施前的审计快照。P0 已将 Snapshot、ChangeSet、规则 Router、fake Runner、路径 Oracle、Evidence-Finding-Decision 链纳入可运行 File Agent 场景；P1 及以后能力仍按表中缺口执行。

状态标签含义：`implemented_and_exercised` 为真实路径且有测试/运行；`interface_only` 为模型或接口存在但无生产消费者；`planned_only` 仅 PRD；`absent` 为代码与依赖中均未发现。

| PRD capability | 当前实现与证据 | 运行状态 | 缺失机制 | MVP 优先级 | 假完成风险 | 验收测试 |
| --- | --- | --- | --- | --- | --- | --- |
| Product Registry | `Product`/`Version`，`Service.create`，SQLite | implemented_and_exercised | stable baseline、版本导入 | P0 | 将 initial 当作稳定版 | 创建并读取相邻版本 |
| Snapshot | `ComponentSnapshot` schema，`domain.py:41-48` | interface_only | 扫描、hash、版本绑定 | P0 | 空 schema 冒充快照 | 两版本产出可比较快照 |
| Change Analyzer | 未发现 | absent | ChangeSet、结构化 diff | P0 | 文件 diff 冒充影响分析 | 已知变更产生断言的 ChangeSet |
| Versioned Memory / stale | 未发现 | absent | 来源、适用版本、stale propagation | P2 | 复用过期结论 | 依赖变更使旧 Evidence stale |
| Requirement-Capability-Test 图 | `Capability.requirement_ids` 与 `EvalCase.capability_ids` | interface_only | 图查询、传播、覆盖计算 | P0 | 关系字段不参与选测 | Skill/permission 变更命中 Case |
| Eval Router | planner 选择全部 Case，`harness.py:58-75` | implemented_and_exercised（非 change-aware） | 风险、理由、skip、升级规则 | P0 | 全量选择被称为智能路由 | 低/高风险选择与 recall 对照 |
| Runner Adapter | 未发现 | absent | runner contract、失败分类、资源清理 | P0 | Handoff 被误称为执行 | fake File Agent 完成 Trial |
| Trial 聚合 | 未发现 | absent | trial spec/count、稳定性统计 | P1 | 单次成功代表稳定 | 三次非确定性 Trial 聚合 |
| Trace 归一化 | 未发现 | absent | tool/event trace schema | P0 | 文本日志冒充证据 | 规范化工具调用序列 |
| 确定性 Oracle | `oracle_kind` 字段，`domain.py:65-70` | interface_only | 可执行 Oracle 与 VerificationResult | P0 | 枚举字段冒充验证 | 越权工具调用被判 fail |
| Evidence | `Evidence` schema，`domain.py:95-103`；运行时 0 条 | interface_only | Execution/Trace/Oracle 到 Evidence producer | P0 | Evidence ID 无来源 | Evidence 引用原始结果与环境 |
| Finding | schema，无创建或读取 | interface_only | 由 Evidence 创建、严重度、状态 | P0 | 报告空 findings | Oracle fail 产生 blocking Finding |
| Release Gate | pending/blocked Decision | implemented_and_exercised（仅保守占位） | 从 Finding/Evidence 重算、policy | P0 | 固定文本决策被当作发布门禁 | Evidence 改变使决策可复算 |
| Checkpoint / resume | `graph.compile()` 无 checkpointer | absent | RunEvent、checkpoint、operation id、resume | P1 | SQLite 记录冒充恢复 | 崩溃后三个恢复场景 |
| Approval / Tool policy | 未发现 | absent | policy、sandbox、approval、audit trace | P0 | Runner 先接入后补安全 | 高风险调用默认拒绝 |
| Replay / Ablation | 未发现 | absent | ReplaySpec、环境/工具固定、单变量替换 | P2 | retry 冒充 replay | 固定工具响应重放同一失败 |
| CLI 闭环 | CLI 到 pending/blocked；`cli.py:38-74` | implemented_and_exercised（不完整） | 导入版本到最终 Evidence Gate | P0 | “prepare report”冒充验收闭环 | P0 场景单命令链 |
| Mutation Benchmark / Meta-Eval | 未发现 | absent | mutation、隐藏切分、程序化 ground truth | P3 | 自生成样本自证 | 隐藏 mutation 统计 recall |

## 结论

现有代码对 PRD 的支撑集中在**产品登记和保守的报告准备**。P0 不应继续横向补 schema；必须先让 Snapshot、ChangeSet、Router、Runner、Oracle、Evidence、Finding、Gate 在同一条本地命令中相连。
