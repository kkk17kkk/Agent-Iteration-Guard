# Agent Iteration Guard

> 项目命令必须使用隔离解释器，而不是系统 Anaconda：`backend\scripts\agentguard-python.ps1 -m pytest -q backend\tests`。默认解释器为 `D:\codexdata\agentguard-venv\Scripts\python.exe`，可用 `AGENTGUARD_PYTHON` 覆盖。该环境已声明并安装 `inspect-ai`；缺包时脚本会显式失败，不会回退到系统 Python。
> 不需要用Github CLI来提交，可直接commit和push到origin main当中。
> 不要硬编码！！！不要仅限于脚本化确定流程跑通后就算交付！！必须调用真实LLM api运行。

Agent Iteration Guard 是一个面向 Tool / Skill 型 Agent 的本地优先评测 Harness。它将版本变更、真实 Agent 运行、独立 Verifier、成对比较、证据、记忆和发布门禁组织为可重放、可审查的闭环。

被测项目始终只在本地 clean clone、worktree 或隔离运行环境中执行：Harness 不会 push、创建 PR、修改 remote、Actions、Secrets 或 Release。控制平面和 `sut_native` 均可通过显式 ProviderBinding 切换至 DeepSeek、OpenAI 或本地 vLLM，三者不会互相回退。目标 Provider 凭据只在 child 运行时按目标声明的变量名注入，不写入 SQLite、Trace、报告或 Git。确定性 fixture 仅用于输入契约、Readiness 和回归测试，不能替代真实 Agent 评测结论。

## 当前能力

- 结构化领域模型与 SQLite 持久化：版本、Case、Trial、Trace、Evidence、Comparison、Memory、ReportManifest 与 Gate。
- Project Intelligence、Evaluation Plan、Scenario Input/Fixture/Readiness、独立 Oracle、不可变 Evidence 和确定性 Release Decision Gate 构成主线闭环。
- 本地目标接入：固定 Git revision、导入可复用环境指纹、preflight，以及 HTTP 或无 shell 的 native-command Runner。
- 外部 Agent 评测：声明式 profile/case、独立 Verifier、同环境的 baseline/candidate paired trial，以及从持久化结果复算的比较和报告。
- 真实 Provider 控制面与中文报告 Agent：请求/响应哈希、token/成本账本、预算上限和失败可见；LLM 不能修改 Verifier、Comparison 或 Release Gate。
- 原生目标 Provider 注入：`sut_native` binding 校验角色、允许主机、预算和运行时凭据；HTTP/command Runner 先清空目标 secret，再注入批准配置，禁止覆盖系统进程环境。目标 LLM fallback 或缺失原生 request id 会使实时 evidence 失败。

## 安装

要求 Python 3.11+。建议将环境与运行数据放在数据盘：

```powershell
git clone https://github.com/kkk17kkk/Agent-Iteration-Guard.git
cd Agent-Iteration-Guard\backend
python -m venv D:\codexdata\venvs\agentguard
D:\codexdata\venvs\agentguard\Scripts\python.exe -m pip install -e ".[dev]"
D:\codexdata\venvs\agentguard\Scripts\agentguard.exe --help
```

复制根目录 `.env.example` 为 `.env`，并仅在需要真实 Agent 运行时配置：

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-flash
```

密钥只在运行时从本地环境读取；不会写入 SQLite、Trace、报告或 Git。

一次实验只需要为选中的后端配置一个 key：DeepSeek 用 `DEEPSEEK_API_KEY`，OpenAI 用 `OPENAI_API_KEY`；本地 vLLM 仅在服务器要求 Authorization header 时设置 `VLLM_API_KEY`。AgentGuard 会将批准 binding 映射为目标所需的临时变量。Gemini 不是当前 ProviderBinding 的后端类型。

## 本地目标接入

先固定被测项目 revision 并创建 manifest：

```powershell
agentguard --format json target init `
  --source D:\codexdata\targets\my-agent `
  --target-id my-agent `
  --application backend.main:app `
  --readiness-path /api/v1/status `
  --required-file backend/main.py `
  --dependency-lock requirements.lock `
  --output D:\codexdata\agentguard-targets\my-agent.json

agentguard --format json target cache import `
  --manifest D:\codexdata\agentguard-targets\my-agent.json `
  --environment D:\codexdata\venvs\my-agent `
  --cache-root D:\codexdata\agentguard-environments

agentguard --format json target golden-path `
  --manifest D:\codexdata\agentguard-targets\my-agent.json `
  --cache-root D:\codexdata\agentguard-environments
```

`target golden-path` 只证明本地运行前提满足。正式结论仍需已批准的 Evaluation Plan、Scenario Readiness、独立 Verifier、真实 paired trials 与比较证据。

生成计划后，可在运行目标前检查所有场景输入和 Fixture：

```powershell
agentguard --format json evaluation readiness `
  --project-id my-agent `
  --plan D:\codexdata\plans\evaluation-plan.json `
  --fixture-root D:\codexdata\fixtures\my-agent
```

Project Pair 评测还可以通过 API 生成并持久化 control-plane Evaluation Plan：

```text
Skill Pair 的真实矩阵通过目标 manifest 声明的 `interaction` command 接入。目标进程每次接收一个 `aig.interaction-request.v1` JSON（stdin），返回一个 `aig.interaction-observation.v1` JSON（stdout），并按 manifest 写出 Trace。独立 Oracle 使用单独的 command 接收同一 trial 的 request/observation，返回 `aig.independent-oracle-result.v1`；Oracle 的 `status=verified` 只表示验证过程完成，产品结果由 `outcome=passed|failed|unresolved` 单独记录。

目标 manifest 的 `interaction` 字段只保存非敏感 command contract：

```json
{
  "command": ["{python}", "evaluate_interaction.py"],
  "timeout_seconds": 120,
  "required_exit_code": 0
}
```

通过通用 CLI 执行完整场景矩阵；它会在启动前执行 Readiness，并强制每个场景覆盖 `a_only`、`b_only`、`combined`：

```powershell
agentguard --format json evaluation interaction-matrix `
  --project-id my-agent `
  --plan D:\\codexdata\\plans\\evaluation-plan.json `
  --manifest D:\\codexdata\\agentguard-targets\\my-agent.json `
  --cache-root D:\\codexdata\\agentguard-environments `
  --fixture-root D:\\codexdata\\fixtures\\my-agent `
  --run-root D:\\codexdata\\runs\\pair-001 `
  --output D:\\codexdata\\runs\\pair-001\\interaction-artifact.json `
  --interaction-name capability_a_and_b `
  --evaluation-id evaluation-001 `
  --oracle-command-part D:\\codexdata\\verifiers\\pair-oracle.exe `
  --oracle-id pair-oracle-v1
```

目标 command 只负责真实 Agent 行为和目标 Trace；Oracle 不读取目标内部判断字段，也不接受 LLM 的 expected behavior 作为 Ground Truth。目标或 Oracle 的进程故障会使矩阵失败并显式暴露，不会静默生成空报告。

POST /api/v1/projects/{project_id}/evaluations/plan
POST /api/v1/projects/{project_id}/evaluations/readiness
POST /api/v1/projects/{project_id}/release-decision
```

本地 GUI 提供 Project Overview、New Evaluation、Running 和 Report 四个工作面：

```powershell
cd frontend
pnpm install
pnpm dev
```

GUI 不生成或替代证据；它只调用主线 API 展示 Project Intelligence、计划、Readiness、报告和确定性 Gate 结果。

## 评测工作流

```text
Target manifest + environment cache
  -> approved profile / case / verifier contracts
  -> real DeepSeek control-plane Agent
  -> Scenario Input / Fixture Readiness
  -> isolated baseline and candidate trials
  -> independent Verifier evidence
  -> deterministic Comparison + Memory + immutable ReportManifest
  -> bounded zh-CN report Agent
```

每个外部项目只将项目语义保留在少量 profile、声明式 case 和 Verifier plugin 中；Runner、Provider、比较、记忆、Gate 与报告层保持项目中性。Provider 或运行环境错误显式记录为基础设施问题，不会静默回退为确定性 Agent 或被测 Agent 故障。

## 开发与验证

```powershell
cd backend
python -m pytest -q tests
python -m compileall -q agentguard
```

启动本地 API：

```powershell
cd backend
uvicorn agentguard.api:app --reload --port 8000
```

## 边界

- v1 聚焦单仓库、可本地运行、具备 Tool / Skill 和可执行 Oracle 的 Agent。
- 不连接真实支付、邮件、删除或部署系统；高风险工具必须使用受控模拟环境。
- 真实 API 运行必须配置 ProviderBinding、允许主机、步骤上限和总预算。
- LLM 可生成受限的分析与报告，不可作为 Ground Truth，也不可越过独立 Verifier 或 Gate。

详细的阶段证据、验证命令和下一步以仓库外 `records/progress.md` 及 `records/PRD-v1.0.md` 为准。
