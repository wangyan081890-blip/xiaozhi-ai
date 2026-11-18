"""在线大模型客户端（OpenAI 兼容协议）。

只依赖 Python 标准库：云端镜像里不需要额外安装 SDK，也不把 API Key 暴露给前端。
支持 DeepSeek / 硅基流动 / 智谱 / OpenAI / 任意 OpenAI 兼容中转。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Iterator

from app.config import ONLINE_MAX_RETRIES, ONLINE_TEMPERATURE, ONLINE_TIMEOUT

_RETRY_STATUS = {429, 500, 502, 503, 504}


class OnlineError(RuntimeError):
    """在线模型调用失败（消息可直接展示给用户）。"""


def _friendly_http_error(status: int, body: str) -> str:
    detail = ""
    try:
        payload = json.loads(body)
        detail = (
            payload.get("error", {}).get("message")
            if isinstance(payload.get("error"), dict)
            else payload.get("message") or payload.get("detail") or ""
        )
    except (ValueError, AttributeError):
        detail = body.strip()[:200]

    if status == 401:
        return "在线模型 API Key 无效或未配置，请检查服务端环境变量"
    if status == 402:
        return "在线模型账户余额不足，请充值后重试"
    if status == 403:
        return "在线模型拒绝访问（403），请检查 Key 权限或模型名"
    if status == 404:
        return "在线模型接口地址或模型名不正确（404）"
    if status == 429:
        return "在线模型请求过于频繁或额度受限，请稍后重试"
    if status >= 500:
        return "在线模型服务暂时不可用，请稍后重试"
    return f"在线模型调用失败（HTTP {status}）{'：' + detail if detail else ''}"


def stream_chat(
    provider: dict,
    messages: list[dict],
    model: str | None = None,
    temperature: float | None = None,
) -> Iterator[dict]:
    """向 OpenAI 兼容接口发起流式请求，产出 delta / thinking / done 事件。"""
    api_key = provider.get("api_key") or ""
    base_url = (provider.get("base_url") or "").rstrip("/")
    model_name = model or provider.get("model") or ""
    if not api_key:
        raise OnlineError("在线模型尚未配置 API Key，请在服务端设置环境变量")
    if not base_url or not model_name:
        raise OnlineError("在线模型尚未配置接口地址或模型名")

    url = f"{base_url}/chat/completions"
    payload = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "temperature": ONLINE_TEMPERATURE if temperature is None else temperature,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "xiaozhi-cloud/2.0",
    }

    last_error: str | None = None
    for attempt in range(max(0, ONLINE_MAX_RETRIES) + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=ONLINE_TIMEOUT)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            last_error = _friendly_http_error(exc.code, body)
            if exc.code in _RETRY_STATUS and attempt < ONLINE_MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise OnlineError(last_error) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            last_error = f"连接在线模型超时或失败：{reason}"
            if attempt < ONLINE_MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise OnlineError(last_error) from exc

        started = False
        usage: dict = {}
        try:
            with response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        chunk = line[5:].strip()
                    else:
                        chunk = line
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"] or usage
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        started = True
                        yield {"type": "thinking", "content": reasoning}
                    content = delta.get("content")
                    if content:
                        started = True
                        yield {"type": "delta", "content": content}
                    finish = choices[0].get("finish_reason")
                    if finish:
                        break
        except (socket.timeout, TimeoutError) as exc:
            raise OnlineError("在线模型响应超时，请重试") from exc
        except urllib.error.URLError as exc:
            raise OnlineError(f"在线模型连接中断：{getattr(exc, 'reason', exc)}") from exc

        if not started:
            raise OnlineError("在线模型没有返回内容，请稍后重试或更换模型")

        stats = {"model": model_name}
        if usage:
            stats["usage"] = usage
        yield {"type": "done", "stats": stats}
        return

    raise OnlineError(last_error or "在线模型调用失败")
