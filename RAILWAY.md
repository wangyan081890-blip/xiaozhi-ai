# Railway 部署（当前线上方案）

小智 AI 目前运行在 **Railway（Docker） + Neon PostgreSQL + DeepSeek**：

- 线上地址：<https://xiaozhi-ai-production.up.railway.app>
- 区域：Singapore；套餐：Free
- 数据库：Neon 免费 PostgreSQL（外部托管，通过 `DATABASE_URL` 连接）

> 免费实例无流量会休眠，首次访问约 10 秒冷启动；这是免费方案的正常行为。

---

## 一、准备

1. Railway 账号：<https://railway.com>（免费版不需要信用卡，每月有少量额度）
2. Neon 数据库：<https://neon.tech> 创建项目，复制连接串，形如
   `postgresql://user:pass@ep-xxx.aws.neon.tech/neondb?sslmode=require`
3. DeepSeek API Key：<https://platform.deepseek.com>
4. 本机安装 Railway CLI（可选，也可全在网页操作）：
   - Windows：从 <https://github.com/railwayapp/cli/releases> 下载 `railway-v*-x86_64-pc-windows-msvc.zip`
   - 解压后把 `railway.exe` 放到 PATH，`railway --version` 验证

## 二、创建项目并部署（CLI）

```bash
# 1. 登录（浏览器授权）
railway login

# 2. 在项目目录初始化（会创建项目并关联当前目录）
railway init

# 3. 上传当前目录并按 Dockerfile 构建部署
railway up
```

> 如果 `railway link` 提示无法写入配置（权限问题），可直接用显式参数：
> `railway up --project <projectId> --environment production`

## 三、配置环境变量

在 Railway 控制台 → 项目 → 服务 → **Variables** 中添加（或 `railway variables --set`）：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 是 | Neon 连接串，`postgresql+psycopg://...?sslmode=require`；也可直接填 `postgres://`，应用会自动归一化 |
| `SECRET_KEY` | 是 | 64 位随机十六进制，用于签名匿名 Cookie；生成后不要更换，否则所有用户身份失效 |
| `DEEPSEEK_API_KEY` | 是 | DeepSeek 密钥（默认模型 `deepseek-chat`） |
| `DEFAULT_PROVIDER` | 否 | 默认 `deepseek` |
| `COOKIE_SECURE` | 否 | 线上填 `true`（HTTPS） |
| `OLLAMA_ENABLED` | 否 | 线上填 `false` |
| `MAX_CONCURRENT` | 否 | 免费实例建议 `2` |
| `AGENT_ENABLED` | 否 | 启用 AI 自主开发系统时填 `true` |
| `GITHUB_TOKEN` | 否 | GitHub PAT（repo 权限），用于建 PR / 派发 workflow |
| `GITHUB_REPO` | 否 | 如 `用户名/xiaozhi-ai` |
| `GITHUB_BRANCH` | 否 | 默认 `main` |
| `DEV_CALLBACK_SECRET` | 否 | 与仓库 Secret 相同的 HMAC 密钥 |
| `APP_BASE_URL` | 否 | 线上地址，用于回调 |
| `RAILWAY_TOKEN` / `RAILWAY_PROJECT_ID` / `RAILWAY_ENVIRONMENT_ID` / `RAILWAY_SERVICE_ID` / `RAILWAY_PUBLIC_URL` | 否 | 自动部署与回滚使用（Actions 部署 workflow 需要同名 Secrets） |

> 开启 AI 开发系统时，还需在 GitHub 仓库 **Settings → Secrets and variables → Actions** 配置
> `DEEPSEEK_API_KEY`、`GH_TOKEN`、`APP_BASE_URL`、`DEV_CALLBACK_SECRET`、`RAILWAY_TOKEN`、
> `RAILWAY_PROJECT_ID`、`RAILWAY_ENVIRONMENT_ID`、`RAILWAY_SERVICE_ID`、`RAILWAY_PUBLIC_URL`。

生成 `SECRET_KEY`：

```powershell
-join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
```

> Railway 会自动注入 `PORT`，Dockerfile 已用 `${PORT:-8000}` 适配，无需手动设置。

## 四、生成公网域名

服务 → **Settings → Networking → Generate Domain**，得到
`https://<服务名>-production.up.railway.app`。

## 五、验证

```bash
# 健康检查（应返回 ok:true 且 dialect 为 postgresql）
curl https://<你的域名>/healthz

# 15 项线上验收（SSE、多用户隔离、Ollama 关闭等）
python scripts/acceptance.py --base https://<你的域名>
```

期望：`共 15 项，通过 15 项，失败 0 项`。

## 六、更新与回滚

- 更新：改代码后重新 `railway up`，Railway 自动构建新版本并切换。
- 回滚：控制台 → Deployments → 选择历史版本 → **Redeploy**。
- 查看日志：控制台 → Deployments → 当前部署 → Logs；或 `railway logs`。

## 七、环境变量修改后

修改 Variables 后，Railway 通常会自动触发一次重启；若没有，在 Deployments 中手动 **Redeploy**。

## 八、免费额度与限制

- 免费 Web 服务在无请求时休眠，冷启动约 10 秒；有请求后自动唤醒。
- 免费额度按月计算，超出后服务会暂停到下一个周期；长期运营建议升级 Hobby 或迁到自有服务器。
- Neon 免费库有存储/计算时间限制；长期不用会自动暂停，可在控制台恢复。

## 九、安全

- 所有密钥只放在 Railway Variables，不写入仓库，不暴露给前端。
- 泄露后立即在服务商控制台吊销并重新生成。
- 生产建议：`COOKIE_SECURE=true`、`OLLAMA_ENABLED=false`、`MAX_CONCURRENT` 保持较小值。

## 十、常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 502 / 部署失败 | 查看 Logs；常见为 `DATABASE_URL` 格式错误或 Neon 未放行 |
| 健康检查 503 | 数据库连接失败（`dialect` 不对/连不上）；检查连接串与 Neon 状态 |
| 页面显示“在线模型未配置” | 未设置 `DEEPSEEK_API_KEY` 或 `DEFAULT_PROVIDER` 指向了未配置的服务 |
| 首次打开很慢 | 免费实例冷启动，等待约 10 秒 |
| Cookie 失效、历史“消失” | 更换过 `SECRET_KEY`，旧签名失效；恢复原值即可 |
