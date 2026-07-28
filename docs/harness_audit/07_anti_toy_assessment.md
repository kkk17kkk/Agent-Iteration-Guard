# Anti-Toy 评估

| 风险 | 当前是否存在 | 严重度 | 代码/运行证据 | 最小修复 | 防回归测试 |
| --- | --- | --- | --- | --- | --- |
| 固定 fixture 假闭环 | 是 | critical | `Service.fixture` 仅建记录；无 Runner | P0 File Agent Runner | Agent 实际执行并生成 Trace |
| 报告先行 | 是 | high | Service 无 Evidence 即建 Decision，`service.py:97-110` | Gate 从 Finding 重算 | 无 Evidence 不得 ready |
| Diff 即智能选测 | 当前无 Diff；风险将随 Phase 2 出现 | high | planner 固定选择所有 Case，`harness.py:64-73` | 显式 ChangeSet→Impact 规则 | known-change selection test |
| LLM-only Judge | 否（当前无 LLM） | low | 未发现 Provider/LLM | 保持 Oracle-first | LLM 不得提升 evidence level |
| Replay 只是重新调用 | 尚不存在 replay | medium | 无 ReplaySpec | 固定输入/环境/工具响应 | replay checksum test |
| Agent 数量装饰 | 部分 | high | 角色只有 Handoff 文本，节点无独立职责 | WorkItem owner + artifact contract | role 无越权写入测试 |
| 日志冒充恢复 | 是 | critical | SQLite 有记录但 checkpointer None，重复调用新建 Run | RunEvent + checkpoint + idempotency | 三类崩溃恢复测试 |
| Evidence 只是字段 | 是 | critical | Evidence 为 0；Decision 无 evidence refs | Evidence-Finding-Decision lineage | Decision 可从 Evidence 重算 |
| Benchmark 自证 | 当前未实现 | medium | 无 mutation/ground truth | 后置到 P3，使用隐藏 mutation | hidden split recall |
| 过早平台化 | 风险受控 | medium | UI/HTTP 存在但小；无插件系统 | P0 前冻结 GUI/provider 扩展 | P0 后才允许第二 Runner |

## 三个最危险的早期偏航点

1. **把 pending 记录误称为 Harness 验收**：当前最接近此风险。`awaiting_evidence` 是诚实状态，但它仍不是运行或验证。
2. **先实现全量 Snapshot/Router 模块，再实现一个 Runner/Oracle**：会使规则没有真实 evidence feedback，最终退化为报告生成器。
3. **把角色节点扩张成多 Agent 演示**：当前角色没有 owner、工具边界或可验证工件；增加 Prompt 只会放大不可控性。

## 保留、删除、推迟

- **保留**：Product/Version、SQLite Store、CLI/API 共用 Service、保守 pending/block gate、LangGraph 与领域层分离。
- **替换而非叠加**：当前 `Handoff` 需降级为审计/解释记录；核心协调改为 WorkItem 与 artifact refs。
- **推迟**：GUI 重构、多个 Provider、SSE、多个装饰性 Agent、向量记忆、通用插件框架、自动根因和大规模 Benchmark。
- **不删除但不计能力**：`Evidence`、`Finding`、`ComponentSnapshot` schemas 可作为 P0 起点；在有 producer/consumer 前不应写入 README 成果。
