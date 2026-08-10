# Public Demo Deployment

这份说明对应 AIG 的 Public Demo：Vercel 提供前端，Railway 提供只读 Demo backend。完整 Docker / local-first 版本仍由 `docker-compose.yml` 保持，Public Demo 不复用开发机文件、Provider 凭据或真实 Agent 执行环境。

## Public Demo 边界

- 默认进入预加载的 LightTable canonical artifacts。
- backend 使用现有 FastAPI/domain contract，仅提供 Demo 报告、Evidence、Release Decision 和导出读取接口。
- `AIG_DEMO_MODE=true` 与 `AIG_DEMO_READ_ONLY=true` 时，backend 拒绝所有非读取请求。
- 不配置 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY` 或 `VLLM_API_KEY`。
- 不开放仓库上传、Provider 配置、真实 evaluation execution、任意 Tool / shell / filesystem 操作。
- Skill Pair 报告从仓库内的 sanitized canonical artifact 提供，不在公网请求中重新生成。

## Railway backend

在 Railway 中从当前 GitHub repository 创建服务，Root Directory 保持 repository root，并使用仓库中的 `railway.json` / `backend/Dockerfile`。

设置以下变量：

```text
AIG_DEMO_MODE=true
AIG_DEMO_READ_ONLY=true
AIG_CORS_ORIGINS=<Vercel frontend URL>
AGENTGUARD_DB=/data/agentguard.db
AGENTGUARD_ASSET_ROOT=/app
AGENTGUARD_UPLOAD_ROOT=/data/uploads
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
VLLM_API_KEY=
```

Railway 会注入 `PORT`；Docker entrypoint 会自动监听该端口。健康检查为 `/health`。Public Demo 只依赖镜像中的 canonical artifacts，因此不依赖持久化用户数据；如启用 Railway Volume，也只能挂载到 `/data`，不能把开发机目录映射到服务中。

部署完成后，在 Railway 生成的公网域名后追加 `/health`，确认返回 `status: ok`，再把该 backend URL 配置到 Vercel。

## Vercel frontend

创建 Vercel Project，连接同一个 repository，并将 Root Directory 设置为 `frontend`。`frontend/vercel.json` 提供 SPA history fallback。

设置构建环境变量：

```text
VITE_API_BASE=<Railway backend URL>
VITE_AIG_DEMO_MODE=true
```

部署后，使用 Vercel 生成的 HTTPS URL 访问 `/`。该 URL 直接加载 LightTable；`?demo=lighttable` 仍可作为显式入口。将最终 Vercel URL 回填到 Railway 的 `AIG_CORS_ORIGINS`，重新部署 backend，并在浏览器中验证页面刷新与 API 请求均正常。

## 发布后验收

至少检查：

1. 首页无需登录即可打开，并显示 `LightTable Demo · 只读`。
2. Project Intelligence、Skill evaluation、Evidence、Report、Release Decision 可以浏览。
3. Skill Pair canonical report 可以打开，并展示 A-only / B-only / A+B matched comparison。
4. 直接刷新 `/` 不返回 404，浏览器 API 请求不指向 localhost、私网地址或 Docker hostname。
5. 对 backend 发起 `POST` / `PUT` / `PATCH` / `DELETE` 返回 403；不存在 Provider secret、repo execution 或 evaluation execution。
6. 本地 Docker 仍使用 `docker compose up --build`，不因 Public Demo 配置改变其默认 local-first 行为。

## 更新流程

更新 canonical artifacts 或代码后，先运行 backend tests、frontend production build 和 Docker smoke test，再推送到 GitHub。Vercel 与 Railway 从同一 commit 重新部署；部署完成后重复上述验收，不在公网手工编辑 SQLite 或报告文件。
