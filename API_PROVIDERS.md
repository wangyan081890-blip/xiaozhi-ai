# 在线模型 API 接入教程

云端版默认调用「OpenAI 兼容」接口。只要服务商提供 `/v1/chat/completions`
（含流式 SSE），就可以接入；切换服务商只需要改环境变量，不需要改代码。

当前内置支持：

| 服务商 | 默认地址 | 默认模型 | 环境变量 |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `Qwen/Qwen3-8B` | `SILICONFLOW_API_KEY` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | `ZHIPU_API_KEY` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | `OPENAI_API_KEY` |
| 任意兼容接口 | 自定义 | 自定义 | `CUSTOM_API_KEY` + `CUSTOM_BASE_URL` + `CUSTOM_MODEL` |

## 一、DeepSeek（推荐，国内直连、便宜）

1. 打开 <https://platform.deepseek.com/> 注册并登录。
2. 进入「API Keys」→ 创建 Key，复制 `sk-...`。
3. 在服务器 `.env` 中填写：

```ini
DEEPSEEK_API_KEY=sk-你的key
DEFAULT_PROVIDER=deepseek
```

4. 重启：`docker compose up -d`，打开页面顶部应显示「在线模型已连接 · DeepSeek」。

可选覆盖：

```ini
# DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
# DEEPSEEK_MODEL=deepseek-chat
```

## 二、硅基流动 SiliconFlow

1. 打开 <https://cloud.siliconflow.cn/> 注册，完成实名认证。
2. 「API 密钥」→ 新建密钥。
3. 配置：

```ini
SILICONFLOW_API_KEY=sk-你的key
DEFAULT_PROVIDER=siliconflow
# SILICONFLOW_MODEL=Qwen/Qwen3-8B
```

适合想跑 Qwen 系列、且需要国内合规发票的场景。

## 三、智谱 GLM

1. 打开 <https://open.bigmodel.cn/> 注册。
2. 控制台 →「API Keys」创建。
3. 配置：

```ini
ZHIPU_API_KEY=你的key
DEFAULT_PROVIDER=zhipu
# ZHIPU_MODEL=glm-4-flash
```

`glm-4-flash` 有免费额度，适合先试跑。

## 四、OpenAI

```ini
OPENAI_API_KEY=sk-...
DEFAULT_PROVIDER=openai
# OPENAI_MODEL=gpt-4o-mini
```

注意：国内服务器直连 `api.openai.com` 可能超时；如需使用，请让服务器具备相应网络条件。

## 五、任意 OpenAI 兼容服务

只要对方提供 `POST {BASE_URL}/chat/completions` 且支持 `stream: true`，
就可以用三项自定义变量接入：

```ini
CUSTOM_API_KEY=...
CUSTOM_BASE_URL=https://your-host/v1
CUSTOM_MODEL=your-model-name
DEFAULT_PROVIDER=custom
```

例如：OneAPI / New API 网关、Azure OpenAI 兼容层、vLLM、Ollama 的 `/v1` 兼容端点、
公司内部模型网关等。

> 注意：`CUSTOM_BASE_URL` 末尾不要带 `/chat/completions`，只写到 `/v1`。

## 六、验证接入是否成功

服务器上：

```bash
docker compose exec app python - <<'PY'
import json, urllib.request, os
base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
key = os.environ["DEEPSEEK_API_KEY"]
req = urllib.request.Request(
    base + "/chat/completions",
    data=json.dumps({
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [{"role": "user", "content": "只回复：ok"}],
        "stream": False,
    }).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
)
print(urllib.request.urlopen(req, timeout=60).read().decode()[:300])
PY
```

或直接打开网页发一句「你好」，看是否流式返回。

## 七、常见错误

| HTTP | 含义 | 处理 |
| --- | --- | --- |
| 401 | Key 无效或过期 | 重新生成 Key，确认没有多余空格 |
| 402 | 余额不足 | 充值 |
| 403 | 无权限 / 未实名 | 完成实名或开通对应模型 |
| 404 | 地址或模型名错误 | 检查 `*_BASE_URL`（到 /v1 为止）与 `*_MODEL` |
| 429 | 频率/额度限制 | 稍后重试；降低 `RATE_LIMIT_PER_MINUTE` 或充值 |
| 5xx | 服务商故障 | 应用会自动重试 2 次；仍失败可切换其他服务商 |

应用会把上述错误转成中文提示，直接显示在聊天气泡里，不暴露 Key。

## 八、多服务商切换

- 配置多个 Key 时，`/api/models` 会列出全部可用模型，页面右上角可切换。
- `DEFAULT_PROVIDER` 决定默认服务商；没有显式设置时，
  自动选择「第一个已配置的在线服务」。
- 前端不会接触任何 Key：所有请求都经服务端转发。

## 九、成本控制建议

- 先用 `glm-4-flash` / `deepseek-chat` 这类低价模型联调。
- 限制 `MAX_HISTORY_MESSAGES`（默认 24）可减少上下文 token。
- 设置 `RATE_LIMIT_PER_MINUTE`、`MAX_CONCURRENT`、`MAX_MESSAGES_PER_SESSION` 防止滥用。
- 在服务商控制台设置「每日预算」或「用量告警」。
