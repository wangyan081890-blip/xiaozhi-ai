"""与本地 Ollama 服务通信的轻量客户端（仅使用 Python 标准库）。

Ollama 默认监听 127.0.0.1:11434，提供原生 /api/chat、/api/tags 等接口。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

from app.config import (
    LOCAL_BASE_URL,
    LOCAL_KEEP_ALIVE,
    LOCAL_MODEL,
    LOCAL_NUM_CTX,
    LOCAL_TEMPERATURE,
    LOCAL_TIMEOUT,
)


class OllamaError(RuntimeError):
    """本地模型服务不可用或返回错误。"""


def _open(path: str, payload: dict | None = None, timeout: float = 5.0):
    url = f"{LOCAL_BASE_URL}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        error = OllamaError(f"本地模型返回 HTTP {exc.code}：{detail}")
        error.status = exc.code  # type: ignore[attr-defined]
        raise error from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise OllamaError(
            "无法连接本地模型服务（Ollama）。"
            "请先运行 start.bat 启动 Ollama，或确认 LOCAL_BASE_URL 是否正确。"
            f"（{reason}）"
        ) from exc


def is_thinking_model(model: str) -> bool:
    name = (model or "").lower()
    return any(token in name for token in ("qwen3", "deepseek-r1", "reasoner", "thinking", "qwq"))


def health() -> dict:
    """检查 Ollama 是否在线，并返回版本与常用信息。"""
    try:
        with _open("/api/version", timeout=2.5) as response:
            version = json.loads(response.read().decode("utf-8")).get("version", "")
        models = list_models()
        return {
            "ok": True,
            "version": version,
            "base_url": LOCAL_BASE_URL,
            "models": len(models),
            "error": None,
        }
    except OllamaError as exc:
        return {
            "ok": False,
            "version": None,
            "base_url": LOCAL_BASE_URL,
            "models": 0,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - 健康检查不应抛出异常
        return {
            "ok": False,
            "version": None,
            "base_url": LOCAL_BASE_URL,
            "models": 0,
            "error": f"本地模型状态未知：{exc}",
        }


def list_models() -> list[dict]:
    """列出本机已安装的模型。"""
    with _open("/api/tags", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))

    models = []
    for item in payload.get("models", []):
        details = item.get("details") or {}
        name = item.get("name") or item.get("model") or ""
        models.append(
            {
                "id": name,
                "name": name,
                "size": item.get("size", 0),
                "family": details.get("family", ""),
                "parameter_size": details.get("parameter_size", ""),
                "quantization": details.get("quantization_level", ""),
            }
        )
    return models


def resolve_model(requested: str | None) -> str:
    """返回一个确实存在的模型名；未安装时退回配置中的默认模型。"""
    installed = {item["id"] for item in list_models()} if health()["ok"] else set()
    if requested and requested in installed:
        return requested
    if LOCAL_MODEL in installed:
        return LOCAL_MODEL
    if installed:
        return sorted(installed)[0]
    return requested or LOCAL_MODEL


def stream_chat(
    messages: list[dict],
    model: str | None = None,
    think: bool | None = None,
    temperature: float | None = None,
    num_ctx: int | None = None,
) -> Iterator[dict]:
    """调用 /api/chat 并按块产出事件。

    事件类型：
      {"type": "thinking", "content": "..."}  思考过程（若开启）
      {"type": "delta", "content": "..."}     正文增量
      {"type": "done", "stats": {...}}        结束统计
    """
    model = model or LOCAL_MODEL
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": LOCAL_TEMPERATURE if temperature is None else temperature,
            "num_ctx": num_ctx or LOCAL_NUM_CTX,
        },
        "keep_alive": LOCAL_KEEP_ALIVE,
    }
    if think is not None and is_thinking_model(model):
        payload["think"] = bool(think)

    try:
        response = _open("/api/chat", payload, timeout=LOCAL_TIMEOUT)
    except OllamaError as exc:
        # 自建/导入的模型可能不支持 think 参数，此时自动退回普通对话
        if "think" in payload and getattr(exc, "status", None) == 400:
            payload.pop("think", None)
            response = _open("/api/chat", payload, timeout=LOCAL_TIMEOUT)
        else:
            raise

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue

            if chunk.get("error"):
                raise OllamaError(f"本地模型出错：{chunk['error']}")

            message = chunk.get("message") or {}
            if message.get("thinking"):
                yield {"type": "thinking", "content": message["thinking"]}
            if message.get("content"):
                yield {"type": "delta", "content": message["content"]}

            if chunk.get("done"):
                yield {
                    "type": "done",
                    "stats": {
                        "model": chunk.get("model", model),
                        "total_duration_ms": round(chunk.get("total_duration", 0) / 1e6),
                        "eval_count": chunk.get("eval_count", 0),
                        "prompt_eval_count": chunk.get("prompt_eval_count", 0),
                    },
                }


def chat(
    messages: list[dict],
    model: str | None = None,
    think: bool | None = None,
    temperature: float | None = None,
) -> tuple[str, dict]:
    """非流式对话，返回 (文本, 统计)。"""
    parts: list[str] = []
    stats: dict = {}
    for event in stream_chat(messages, model=model, think=think, temperature=temperature):
        if event["type"] == "delta":
            parts.append(event["content"])
        elif event["type"] == "done":
            stats = event.get("stats", {})
    return "".join(parts), stats
