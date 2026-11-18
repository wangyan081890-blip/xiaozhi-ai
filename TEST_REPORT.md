# 在线测试报告

- 测试日期：2026-11-18
- 版本：小智 AI 云端版 2.0.0
- 结论：**核心链路已在本地端到端验证通过；公网部署尚未执行**（缺服务器与 API Key，详见文末）。

## 一、测试环境

| 项 | 值 |
| --- | --- |
| 操作系统 | Windows 11（本机） |
| Python | 3.14.7 |
| Web 框架 | FastAPI + uvicorn |
| ORM | SQLAlchemy 2.1.9（Core） |
| 数据库 A | SQLite（本地文件） |
| 数据库 B | PostgreSQL 16.2（本机临时实例，端口 55432） |
| 驱动 | psycopg 3.3.9 |
| 在线模型 | `scripts/mock_openai.py` 模拟 OpenAI 兼容接口（127.0.0.1:11436） |
| 浏览器自动化 | Microsoft Edge headless + CDP |
| 测试脚本 | `scripts/test_cloud.py`、`scripts/ui_smoke.mjs` |

> 未使用真实在线模型 Key（本机没有任何服务商 Key），因此测试的是
> 「协议 + 流式 + 业务逻辑」；真实服务商只需替换 Base URL/Key/模型名。

## 二、测试结果

### 1. 服务状态与默认服务商

```bash
python scripts/test_cloud.py --base http://127.0.0.1:8200 --phase1
```

输出：

```
STATUS custom mock-model True
```

- `/api/status` 返回 `default_provider=custom`、`default_model=mock-model`、`online_configured=true` ✅

### 2. 流式对话（SSE）

```
EVENTS ['start', 'delta', 'delta', 'delta', 'delta', 'delta'] ... ['done', 'end']
ANSWER （模拟在线模型）收到：你好，做个自我介绍。这是流式测试回答。
```

- 事件顺序正确：`start → delta* → done → end` ✅
- 内容完整、UTF-8 中文正常 ✅

### 3. 非流式接口

```
NONSTREAM （模拟在线模型）收到：这是非流式测试。
```

- `POST /api/chat` 返回 `success=true` 且内容正确 ✅

### 4. 历史与会话归属

```
HISTORY ['user', 'assistant']
```

- 消息按顺序入库，角色正确 ✅
- 会话标题取首条用户消息 ✅

### 5. 多用户隔离

```
ISOLATION ok, userB sessions: 0
```

- 用户 B（独立 Cookie）看不到用户 A 的会话 ✅
- 用户 B 访问用户 A 的 `session_id` 历史 → HTTP 403 ✅

### 6. Ollama 关闭时的降级

```
OLLAMA-DISABLED 503 本地 Ollama 已关闭（云端版仅作为开发测试选项）
```

- 请求 `provider=ollama` 返回 503 与中文提示 ✅

### 7. 重启后历史保留（SQLite）

```
PHASE1 OK
# 重启 uvicorn
PHASE2 OK: history persisted across restart
AFTER-RESTART ['user', 'assistant', 'user', 'assistant']
```

✅

### 8. 重启后历史保留（PostgreSQL 16.2）

同一套测试以
`DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/xiaozhi`
运行：

```
PHASE1 OK
PHASE2 OK: history persisted across restart
AFTER-RESTART ['user', 'assistant', 'user', 'assistant']
```

✅ PostgreSQL 方言、建表、索引、读写全部正常。

### 9. PostgreSQL 表结构核对

实际建出的表与索引：

```
users(id, created_at, last_seen)
sessions(id, user_id, title, created_at, updated_at)
messages(id, session_id, provider, role, content, created_at)

索引：messages_pkey, ix_messages_session, sessions_pkey, ix_sessions_user, users_pkey
```

- 时间列为 `timestamp with time zone` ✅
- `messages.session_id` 外键 `ON DELETE CASCADE` 实测：
  删除会话后该会话消息数 = 0 ✅

### 10. 前端 UI 冒烟（Edge headless）

```
STATUS 在线模型已连接 · 自定义接口
MODELS [ 'mock-model' ]
ANSWER 在线推理 · 30 字 || （模拟在线模型）收到：你好，做个自我介绍。这是流式测试回答。
EXCEPTIONS []
```

- 状态栏正确显示在线模型名称 ✅
- 模型下拉可用 ✅
- 发送消息 → 流式渲染 → 元信息「在线推理」✅
- 无 JS 异常 ✅

### 11. 线上验收脚本（本地自测）

新增 `scripts/acceptance.py`，对任意部署地址跑 15 项端到端检查：

```
[PASS] healthz
[PASS] 首页 / 移动端 viewport
[PASS] 前端脚本 / 萌宠资源
[PASS] 在线模型已配置 / Ollama 已关闭
[PASS] SSE 事件顺序
[PASS] 回答非空
[PASS] 历史落库（2 条）
[PASS] 连续对话（上下文 4 条）
[PASS] 用户 B 无会话
[PASS] 越权访问被拒（HTTP 403）
[PASS] Ollama 关闭降级（HTTP 503）
共 15 项，通过 15 项，失败 0 项。
```

该脚本在真实上线后可直接对公网地址执行，作为最终验收证据。

## 三、复现方式

```bash
# 1) 启动模拟在线模型
python scripts/mock_openai.py

# 2) 启动应用（SQLite）
set CUSTOM_API_KEY=test-key
set CUSTOM_BASE_URL=http://127.0.0.1:11436/v1
set CUSTOM_MODEL=mock-model
set DEFAULT_PROVIDER=custom
python -m uvicorn app.main:app --port 8200

# 3) 测试
python scripts/test_cloud.py --base http://127.0.0.1:8200 --phase1
# 重启应用后
python scripts/test_cloud.py --base http://127.0.0.1:8200 --phase2

# 4) 线上验收（部署后对公网地址执行）
python scripts/acceptance.py --base https://<你的域名>
```

PostgreSQL 版只需追加：

```bash
set DATABASE_URL=postgresql+psycopg://用户:密码@127.0.0.1:5432/xiaozhi
```

## 四、尚未验证的部分（诚实说明）

| 项目 | 原因 | 状态 |
| --- | --- | --- |
| Docker 镜像构建 / compose 启动 | 本机未安装 Docker | 未运行 |
| Caddy 自动 HTTPS | 需要公网域名与服务器 | 未运行 |
| 真实在线模型（DeepSeek 等） | 没有任何服务商 API Key | 未调用 |
| 公网服务器部署 | 无云账号 / 无服务器 | **未部署** |
| 手机真机访问 | 需部署后验证 | 未验证（响应式布局已在 localhost 用 390×844 模拟） |

这些属于「部署阶段」验证项：拿到服务器与 Key 后，按
[DEPLOY.md](DEPLOY.md) 执行，我可以继续做线上验收并补全本报告。

## 五、已知限制

- 身份为匿名 Cookie，不是账号体系；清 Cookie 后无法找回旧会话（数据仍在库中）。
- 限流为单进程内存实现，多 worker/多实例时需换成 Redis。
- 并发保护 `BoundedSemaphore` 也是单进程级。
- SQLite 仅适合本地开发，生产请使用 PostgreSQL。
