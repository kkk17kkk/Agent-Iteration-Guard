# Agent Iteration Guard

Agent Iteration Guard 是一个面向 Tool / Skill 型 Agent 的本地优先评测 Harness。它将版本变更、真实 Agent 运行、独立 Verifier、成对比较、证据、记忆和发布门禁组织为可重放、可审查的闭环。

被测项目始终只在本地 clean clone、worktree 或隔离运行环境中执行：Harness 不会 push、创建 PR、修改 remote、Actions、Secrets 或 Release。真实评测使用 DeepSeek API；确定性 fixture 仅用于单元和回归测试，不能替代真实 Agent 评测结论。

## 当前能力

- 结构化领域模型与 SQLite 持久化：版本、Case、Trial、Trace、Evidence、Comparison、Memory、ReportManifest 与 Gate。
- Stage 1/2 已并入主线执行路径：Stage 1 负责可执行 Oracle、故障注入、重放和消融门禁；Stage 2 通过 DeepSeek 原生 tool calling 驱动真实本地工具循环。
- 本地目标接入：固定 Git revision、导入可复用环境指纹、preflight，以及 HTTP 或无 shell 的 native-command Runner。
- 外部 Agent 评测：声明式 profile/case、独立 Verifier、同环境的 baseline/candidate paired trial，以及从持久化结果复算的比较和报告。
- 真实 Provider 控制面与中文报告 Agent：请求/响应哈希、token/成本账本、预算上限和失败可见；LLM 不能修改 Verifier、Comparison 或 Release Gate。

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

`target golden-path` 只证明本地运行前提满足。正式结论仍需已批准的 case、独立 Verifier、真实 paired trials 与比较证据。

## 评测工作流

```text
Target manifest + environment cache
  -> approved profile / case / verifier contracts
  -> real DeepSeek control-plane Agent
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
