---
title: 小智 AI
emoji: 🤖
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
license: mit
---

# 小智 AI · 云端在线版

小智 AI 从「本机 Ollama」升级为可公网访问的多用户在线助手：浏览器打开网页即可对话，
推理在云端调用在线大模型（OpenAI 兼容接口），聊天记录保存在云端数据库，按匿名用户隔离。

> **已上线**：<https://xiaozhi-ai-production.up.railway.app>（Railway + Neon PostgreSQL + DeepSeek）。
> 仓库同时保留完整自托管产物（Dockerfile、docker-compose、Caddy HTTPS），可在自有服务器复现；
> 仓库本身不含任何真实 API Key。

## 一、功能

- 在线大模型为默认推理后端（DeepSeek / 通义千问 / 硅基流动 / 智谱 GLM / OpenAI / 任意兼容接口），
  支持流式输出、超时与重试、中文错误提示。
- 保留本地 Ollama 作为开发测试选项（`OLLAMA_ENABLED=true` 时启用）。
- 多用户：匿名签名 Cookie 身份，每个浏览器独立会话与历史；跨用户访问返回 403。
- 连续对话：历史存 PostgreSQL（云端）或 SQLite（本地开发），重启不丢。
- 前端：响应式聊天界面、Markdown/代码高亮、复制/重新生成/停止、深浅色主题。
- 萌宠助手：点击互动、长按拖动、连点三下进入「小智跳台阶」小游戏（桌面与手机均可用）。
- 运维：缓存控制、限流、并发保护、健康检查；Railway 自动重启与 HTTPS（自托管时由 Docker + Caddy 负责）。

## 二、架构

```
浏览器（手机/电脑/平板）
        │ HTTPS
        ▼
   Railway 边缘（自动 HTTPS）/ 或自托管 Caddy
        │ HTTP :PORT
        ▼
 FastAPI 应用（uvicorn）──► 在线大模型 API（OpenAI 兼容，当前 DeepSeek）
        │
        ▼
   PostgreSQL（Neon 云库；自托管为 PG 16）
```

## 三、目录结构

```
xiaozhi-cloud/
├── app/
│   ├── main.py         # FastAPI 路由、SSE、限流、并发、缓存头
│   ├── config.py       # 环境变量与服务商注册表
│   ├── online.py       # 在线模型流式客户端（标准库 urllib）
│   ├── providers.py    # 在线 / Ollama 统一调度
│   ├── db.py           # SQLAlchemy Core，SQLite / PostgreSQL
│   ├── auth.py         # HMAC 签名匿名 Cookie
│   ├── schemas.py      # 请求校验
│   └── ollama.py       # 本地测试用 Ollama 客户端
├── static/             # 前端（index.html / app.js / style.css / assets）
├── scripts/
│   ├── mock_openai.py  # 本地模拟 OpenAI 接口（联调用）
│   ├── test_cloud.py   # 端到端测试（phase1 / phase2）
│   ├── acceptance.py   # 线上验收（15 项）
│   ├── deploy_hf.py    # HF Spaces 一键部署（备选）
│   └── deploy_render.py # Render 一键部署
├── Dockerfile
├── docker-compose.yml
├── Caddyfile
├── render.yaml         # Render Blueprint（备用）
├── .env.example
├── RAILWAY.md          # Railway 实际部署记录与复现步骤（当前线上方案）
├── RENDER.md           # Render 部署教程（备用，需绑卡）
├── HUGGINGFACE.md      # HF Spaces 备选方案
├── DEPLOY.md           # 自有服务器部署教程
├── POSTGRES.md         # 数据库设计与迁移
└── requirements.txt
```

## 四、快速开始

### 方式 A：Docker Compose（推荐，云端/本机均可）

```bash
cp .env.example .env
# 编辑 .env：至少填 POSTGRES_PASSWORD、SECRET_KEY、一个在线模型 API Key
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8000/healthz
```

- 本地调试：`SITE_ADDRESS` 留空，访问 `http://服务器IP/`。
- 正式域名：`SITE_ADDRESS=xiaozhi.example.com`，DNS A 记录指向服务器，
  Caddy 会自动申请 HTTPS 证书。

详细步骤见 [DEPLOY.md](DEPLOY.md)。

### 方式 B：本地开发（SQLite + 模拟接口）

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS / Linux

# 终端 1：模拟在线模型
python scripts/mock_openai.py

# 终端 2：启动应用（用自定义接口指向 mock）
set CUSTOM_API_KEY=test-key
set CUSTOM_BASE_URL=http://127.0.0.1:11436/v1
set CUSTOM_MODEL=mock-model
set DEFAULT_PROVIDER=custom
python -m uvicorn app.main:app --host 127.0.0.1 --port 8200
```

打开 <http://127.0.0.1:8200/>。

运行端到端测试：

```bash
python scripts/test_cloud.py --phase1
# 重启应用后
python scripts/test_cloud.py --phase2
```

## 五、环境变量

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `SITE_ADDRESS` | Caddy 站点地址；留空为 `:80`，填域名自动 HTTPS | 空 |
| `POSTGRES_PASSWORD` | PostgreSQL 密码（compose 使用） | 无，必填 |
| `DATABASE_URL` | 数据库连接串；compose 自动拼 PostgreSQL | SQLite |
| `SECRET_KEY` | Cookie 签名密钥，生产必须固定 | 自动生成到 data/.secret_key |
| `COOKIE_SECURE` | `auto` / `true` / `false` | `auto` |
| `DEEPSEEK_API_KEY` 等 | 各在线服务商密钥 | 空 |
| `DEFAULT_PROVIDER` | 默认服务商 | 首个已配置在线服务 |
| `OLLAMA_ENABLED` | 是否启用本地 Ollama 测试 | `false` |
| `MAX_HISTORY_MESSAGES` | 携带的历史消息条数 | 24 |
| `RATE_LIMIT_PER_MINUTE` | 每 IP 每分钟请求上限 | 30 |
| `MAX_CONCURRENT` | 同时生成的最大请求数 | 4 |

完整清单见 [.env.example](.env.example)。

## 六、接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 聊天页面 |
| GET | `/healthz` | 健康检查（含数据库） |
| GET | `/api/status` | 服务状态、默认模型、服务商配置 |
| GET | `/api/models` | 可用模型列表 |
| GET | `/api/providers` | 服务商列表 |
| GET | `/api/me` | 当前匿名用户 |
| GET / POST | `/api/sessions` | 会话列表 / 新建会话 |
| GET | `/api/history?session_id=` | 会话历史（越权 403） |
| DELETE | `/api/session?session_id=` | 删除会话 |
| POST | `/api/chat/stream` | SSE 流式对话 |
| POST | `/api/chat` | 非流式对话 |

## 七、多用户与数据隔离

- 首次访问分配随机 uid，写入 `xz_uid` Cookie（HttpOnly、SameSite=Lax、HTTPS 下 Secure），
  内容为 `uid.HMAC(uid)`，服务端校验签名，无法伪造他人 uid。
- 每个会话归属一个 `user_id`；读写历史前校验归属，越权返回 403。
- 同一浏览器的多个标签共享身份；换浏览器 / 清 Cookie 会得到新的匿名身份（数据仍在库中）。
- 注意：这是「匿名隔离」，不是账号体系。如需登录、找回历史、跨设备同步，
  后续可在 `users` 表上扩展邮箱/密码或第三方登录。

## 八、安全说明

- API Key 只存在于服务端环境变量，前端永远拿不到。
- 页面禁止缓存（`no-store`），静态资源 `no-cache`。
- 默认限流 30 次/分钟/IP，并发 4，单条消息 4000 字符，单会话最多 4000 条。
- Caddy 自动 HTTPS；容器以非 root 用户运行；数据库不对公网暴露端口。
- 生产环境请务必：固定 `SECRET_KEY`、使用强数据库密码、只开放 80/443。

## 九、当前状态（诚实说明）

- **已部署到公网**（2026-11-19）：<https://xiaozhi-ai-production.up.railway.app>
  - 平台：Railway 免费 Web 服务（Docker，新加坡区，自动 HTTPS，域名 `*.up.railway.app`）
  - 数据库：Neon 免费 PostgreSQL（`sslmode=require`），会话与消息持久化
  - 模型：DeepSeek `deepseek-chat`；API Key 仅存于 Railway 环境变量，不进入仓库与前端
- 线上验收：`scripts/acceptance.py` **15/15 通过**（健康检查、首页与移动端 viewport、静态资源、
  在线模型已配置、Ollama 关闭、SSE 事件顺序、回答非空、历史 2 条、连续对话 4 条、
  用户 B 无会话、越权 403、Ollama 503）。
- 另做重启持久化测试：同一浏览器 Cookie 在「重新部署」后仍能读取此前历史 → Neon 持久化生效。
- 免费额度注意：Railway 免费实例无流量时会休眠，首次打开约 10 秒冷启动；
  Neon 免费库有存储与连接数上限。长期公开运营建议升级付费，或迁到自有服务器（[DEPLOY.md](DEPLOY.md)）。

## 十、文档

- [RAILWAY.md](RAILWAY.md)：Railway 实际部署记录、环境变量与复现步骤（当前线上方案）
- [RENDER.md](RENDER.md)：Render 部署教程（备用；创建资源需绑卡）
- [HUGGINGFACE.md](HUGGINGFACE.md)：备选免费方案（HF Spaces 需 PRO 才能跑 Docker）
- [DEPLOY.md](DEPLOY.md)：自有服务器部署（Docker、域名、HTTPS、验证、备份、更新）
- [POSTGRES.md](POSTGRES.md)：表结构、索引、SQLite 迁移、备份恢复
- [API_PROVIDERS.md](API_PROVIDERS.md)：各在线模型接入步骤与错误排查
- [TEST_REPORT.md](TEST_REPORT.md)：本地端到端测试报告与未验证项
- [.env.example](.env.example)：环境变量模板

## 许可证

仅用于学习与个人项目；在线模型服务的使用需遵守各服务商条款。
