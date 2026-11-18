# 用 Render 部署小智 AI（云端在线版）

Render 是免费 PaaS：连上 GitHub 仓库即可构建 Docker 镜像、分配 HTTPS 域名、附带免费 PostgreSQL。
本项目的 `Dockerfile` 已兼容 Render（监听 Render 注入的 `PORT`，默认 10000），
`app/config.py` 会把 Render 给出的 `postgres://...` 自动转换成 SQLAlchemy 可用的
`postgresql+psycopg://...`。

> 免费额度提醒：免费 Web 实例 **15 分钟无请求会休眠**，下次访问需 30–60 秒冷启动；
> 免费 PostgreSQL **30 天后到期**，到期前可在 Render 升级，或把 `DATABASE_URL` 换到 Neon。
> 这些都只影响体验，不影响“别人打开网址就能用”。

---

## 一、准备账号

| 需要 | 用途 | 地址 |
| --- | --- | --- |
| Render 账号 | 托管 Web + 数据库 | https://render.com （可用 GitHub 登录） |
| GitHub 账号 | 存放代码，Render 从仓库构建 | https://github.com |
| DeepSeek API Key | 在线模型 | https://platform.deepseek.com → API Keys |

### 部署前自检（约 10 秒）

```bat
python scripts\check_deploy_ready.py
```

它会检查：Dockerfile 是否监听 `$PORT`、健康检查是否为 `/healthz`、`requirements.txt`
是否含 `psycopg`、`render.yaml` 结构、待上传文件大小，以及是否误把真实 Key 写进仓库。
看到“自检通过，可以交给 Render 构建”再继续。

---

## 二、方式 A：Blueprint（推荐，最少点击）

1. **把代码传到 GitHub**（没有 git 也可以）：
   - GitHub → New repository → 名称 `xiaozhi-ai` → Private → Create；
   - 进入仓库 → Add file → Upload files → 把本项目**除 `.venv`、`data`、`runtime`、`tmp*` 之外**的文件/文件夹拖进去 → Commit。
   - 或者直接在本机执行（如果以后装了 git）：
     ```bat
     git init && git add . && git commit -m "xiaozhi cloud"
     git remote add origin https://github.com/<你>/xiaozhi-ai.git
     git push -u origin main
     ```
2. Render Dashboard → **New → Blueprint** → 选择该仓库 → **Apply**。
3. Render 读取仓库根目录的 `render.yaml`，自动创建：
   - Web 服务 `xiaozhi-ai`（Docker、free、Singapore、健康检查 `/healthz`）
   - PostgreSQL `xiaozhi-db`（free、16）
4. 在弹出的环境变量里填写 `DEEPSEEK_API_KEY`（`sync: false` 的变量需要手填）。
5. 等待首次构建 + 部署完成（约 3–8 分钟），拿到地址：
   `https://xiaozhi-ai-xxxx.onrender.com`

---

## 三、方式 B：手动创建（不用 Blueprint）

### 1. 创建 PostgreSQL

Render Dashboard → **New → PostgreSQL**：

| 项 | 值 |
| --- | --- |
| Name | `xiaozhi-db` |
| Database | `xiaozhi` |
| User | `xiaozhi` |
| Region | Singapore（与 Web 服务一致） |
| Plan | Free |

创建完成后复制 **Internal Database URL**（形如 `postgres://xiaozhi:...@dpg-xxx-a/xiaozhi`）。

### 2. 创建 Web Service

Render Dashboard → **New → Web Service** → 选择 GitHub 仓库：

| 项 | 值 |
| --- | --- |
| Name | `xiaozhi-ai` |
| Runtime | Docker |
| Region | Singapore |
| Branch | `main` |
| Plan | Free |
| Health Check Path | `/healthz` |
| Dockerfile Path | `./Dockerfile` |

### 3. 环境变量

在 Web 服务 → Environment 中添加：

| Key | Value |
| --- | --- |
| `DATABASE_URL` | 上一步的 Internal Database URL（`postgres://` 开头即可，程序会自动改写） |
| `SECRET_KEY` | 随机 64 位十六进制，例如 `python -c "import secrets;print(secrets.token_hex(32))"` |
| `DEEPSEEK_API_KEY` | `sk-...` |
| `DEFAULT_PROVIDER` | `deepseek` |
| `COOKIE_SECURE` | `true` |
| `OLLAMA_ENABLED` | `false` |
| `MAX_CONCURRENT` | `2` |

> 不要把任何 Key 写进前端或提交到仓库；Render 的环境变量只在服务端可见。

保存后 **Manual Deploy → Deploy latest commit**，等待状态变为 **Live**。

---

## 四、方式 C：脚本一键部署（需要 API Key）

适合不想在网页上点来点去的情况。脚本会：创建 Render PostgreSQL → 创建 Docker Web 服务 →
写入环境变量 → 触发部署 → 轮询 `/healthz`。若提供 `GITHUB_TOKEN` 且没有现成仓库，
它还会用 GitHub API 建私有仓库并逐个文件上传。

```bat
set RENDER_API_KEY=rnd_xxxxxxxx
set DEEPSEEK_API_KEY=sk-xxxxxxxx
set GITHUB_TOKEN=github_pat_xxxxxxxx
set RENDER_REGION=singapore

python scripts\deploy_render.py --check      :: 只校验
python scripts\deploy_render.py --dry-run    :: 打印计划
python scripts\deploy_render.py              :: 真正部署
```

已有仓库时：

```bat
python scripts\deploy_render.py --repo-url https://github.com/<你>/xiaozhi-ai
```

> Render API Key：Dashboard → Account Settings → API Keys。
> GitHub Token：Settings → Developer settings → Fine-grained tokens，
> 需要 `Contents: Read and write`（建仓还需 `Administration: Read and write`）。

---

## 五、验收（必须真实执行）

```bat
python scripts\acceptance.py --base https://xiaozhi-ai-xxxx.onrender.com
```

15 项全部 `PASS` 后，再用手机/另一个浏览器人工确认：

1. 打开地址 → 出现小智界面，状态「在线模型已连接 · DeepSeek」；
2. 提问 → 流式回答；
3. 刷新 → 历史还在；
4. 在 Render 控制台 **Restart** 服务，等重启完成 → 历史仍在（PostgreSQL 持久化）；
5. 手机访问同一地址 → 正常；
6. 换无 Cookie 的浏览器 → 看不到别人的会话（匿名用户隔离）。

---

## 六、免费额度与限制

| 项 | 免费版 |
| --- | --- |
| Web 实例 | 512 MB 内存，CPU 共享；**15 分钟无请求休眠** |
| 冷启动 | 约 30–60 秒（首次请求） |
| PostgreSQL | 256 MB，**30 天到期**（到期前升级/迁移） |
| 出站流量 | 100 GB/月 |
| 构建 | 免费，单次构建有超时限制 |

建议：先把 `MAX_CONCURRENT=2`；若访问量上来，升级 Starter（$7/月起）+ 付费 Postgres。

---

## 七、常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 构建失败：`No such file: Dockerfile` | 仓库根目录必须有 `Dockerfile`；Blueprint 里 Dockerfile Path 用 `./Dockerfile` |
| 部署 Live 但打开 502 | 健康检查未过：看 Logs，通常是 `DATABASE_URL` 不对或 `DEEPSEEK_API_KEY` 缺失 |
| `ModuleNotFoundError: psycopg` | `requirements.txt` 必须有 `psycopg[binary]>=3.2.3`，重新部署 |
| 数据库连接超时 | Web 与 PG 必须在**同一 Region**；用 Internal URL |
| 回答 401/402 | DeepSeek Key 无效或余额不足；402 表示欠费 |
| 首页一直「正在连接云端模型…」 | 看 `/api/status` 的 `online_configured`；未配置会显示横幅 |
| 第一次打开很慢 | 免费实例休眠后的冷启动，属正常现象 |
| 30 天后数据库消失 | 免费 PG 到期；升级或切到 Neon（方案见 `HUGGINGFACE.md` 的 Neon 部分） |

---

## 八、安全要点

- `SECRET_KEY` 只放 Render 环境变量，泄露后所有匿名身份 cookie 可被伪造；
- `COOKIE_SECURE=true`（Render 全站 HTTPS）；
- 不提交 `.env`、`config.env`、`data/`、`runtime/`；
- 免费实例日志保留时间有限，排查要趁早；
- 如需固定域名：Render 支持 Custom Domain，绑定后自动签发证书。
