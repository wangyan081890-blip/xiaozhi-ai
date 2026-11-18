# 部署到 Hugging Face Spaces（免费方案）

本方案用两个免费服务，个人电脑关机后也能访问：

| 组件 | 服务 | 免费额度 | 作用 |
| --- | --- | --- | --- |
| 应用托管 | [Hugging Face Spaces](https://huggingface.co/spaces)（Docker） | 2 vCPU / 16 GB，永久免费，闲置 48h 休眠，访问自动唤醒 | 运行 FastAPI + 前端 |
| 数据库 | [Neon](https://neon.tech)（PostgreSQL） | 0.5 GB 存储，永久免费 | 多用户会话与聊天记录持久化 |
| 大模型 | [DeepSeek 开放平台](https://platform.deepseek.com/) | 新用户有赠送额度，按 token 计费 | 在线推理 |

最终地址形如：`https://<你的HF用户名>-xiaozhi-ai.hf.space`（HTTPS 自动）。

---

## 一、准备三个凭据

### 1. Hugging Face Token

1. 注册 / 登录 <https://huggingface.co/join>
2. 打开 <https://huggingface.co/settings/tokens> → **New token**
3. 类型选 **Write**，名字随意（如 `xiaozhi-deploy`），复制 `hf_...`

### 2. Neon 数据库连接串

1. 注册 / 登录 <https://neon.tech>
2. **New Project** → 区域选 Singapore（离国内近）→ 创建
3. 在 **Connection string** 里复制形如：
   `postgresql://neondb_owner:xxxx@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`

> 免费版数据库闲置会自动休眠，首次查询会唤醒（约 1 秒），属正常现象。

### 3. DeepSeek API Key

1. 登录 <https://platform.deepseek.com/> → **API keys** → 创建，复制 `sk-...`
2. 账户需有余额（新用户注册通常有赠送额度，以官网为准）
3. 默认模型 `deepseek-chat`（也可用 `deepseek-reasoner`，在 Space 变量里改 `DEEPSEEK_MODEL`）

---

## 二、执行部署（二选一）

### 方式 A：在项目根目录运行脚本（推荐）

```bat
set HF_TOKEN=hf_xxxxxxxx
set DATABASE_URL=postgresql://neondb_owner:xxxx@ep-xxx.neon.tech/neondb?sslmode=require
set DEEPSEEK_API_KEY=sk-xxxxxxxx
python scripts/deploy_hf.py
```

脚本会自动：创建 Space → 上传代码 → 写入 Secrets → 重启 → 轮询 `/healthz` → 打印公网地址。
首次构建约 3～8 分钟。

### 方式 B：手动在网页上创建

1. <https://huggingface.co/new-space> → 名称 `xiaozhi-ai` → SDK 选 **Docker** → Public
2. 把本项目文件上传到该 Space（排除 `.venv`、`data`、`config.env`）
3. Space 设置 → **Variables and secrets** 添加：

| 名称 | 类型 | 值 |
| --- | --- | --- |
| `DATABASE_URL` | Secret | Neon 连接串 |
| `SECRET_KEY` | Secret | 随机 64 位十六进制 |
| `DEEPSEEK_API_KEY` | Secret | DeepSeek Key |
| `DEFAULT_PROVIDER` | Variable | `deepseek` |
| `COOKIE_SECURE` | Variable | `true` |
| `OLLAMA_ENABLED` | Variable | `false` |

4. 等待构建完成，访问 `https://<用户名>-xiaozhi-ai.hf.space`

---

## 三、上线验收

先跑自动验收（15 项，包含流式、隔离、越权、降级）：

```bat
python scripts\acceptance.py --base https://<用户名>-xiaozhi-ai.hf.space
```

全部 PASS 后，在浏览器人工确认：

1. 打开首页 → 状态应显示「在线模型已连接 · DeepSeek」
2. 发一条消息 → 应流式返回
3. 刷新页面 → 历史仍在（Neon）
4. 在 Space 页面点 **Restart**，等重启完成后再刷新 → 历史仍在（验证持久化）
5. 用手机浏览器打开同一地址 → 应正常
6. 换一个浏览器（无 Cookie）→ 看到的是独立的新会话，看不到别人的记录

## 四、常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 构建失败 | Dockerfile/依赖问题 | 看 Space 的 Build logs |
| 502 / 一直转圈 | 应用未监听 8000 或启动慢 | 确认 README 里 `app_port: 8000`；看 Container logs |
| 状态「在线模型未配置」 | Secret 名字写错或没重启 | 检查 `DEEPSEEK_API_KEY`，改完点 Restart |
| `401 Unauthorized` | Key 无效/欠费 | 到 DeepSeek 开放平台控制台确认 |
| 数据库连接失败 | 连接串少了 `sslmode=require` | Neon 连接串需带 SSL 参数 |
| 闲置后首次打开很慢 | 免费 Space 休眠 | 正常，等待 30～60 秒 |

## 五、费用与限制

- HF Spaces 免费 CPU：永久免费，闲置 48 小时休眠，唤醒需几十秒；不能跑 GPU。
- Neon 免费：0.5 GB，超出后需升级。
- DeepSeek：按 token 计费，新用户有免费额度；建议在控制台设置消费限额。
- 免费实例为共享 CPU，并发高时会排队（应用内 `MAX_CONCURRENT` 默认 4，HF 上建议 2）。
