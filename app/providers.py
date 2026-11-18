"""模型调度：在线大模型（默认）与本地 Ollama（测试选项）。"""
from __future__ import annotations

from typing import Iterator

from app import ollama, online
from app.config import DEFAULT_PROVIDER, OLLAMA_ENABLED, PROVIDERS


class ProviderError(RuntimeError):
    """服务商不可用 / 未配置。"""


def get_provider(provider_name: str) -> dict:
    name = provider_name or DEFAULT_PROVIDER
    config = PROVIDERS.get(name)
    if not config:
        raise ProviderError("不支持的模型服务商")
    if config["kind"] == "ollama":
        if not OLLAMA_ENABLED:
            raise ProviderError("本地 Ollama 已关闭（云端版仅作为开发测试选项）")
        if not config.get("base_url") or not config.get("model"):
            raise ProviderError("本地模型尚未配置")
        return config
    if not config.get("configured"):
        raise ProviderError(
            f"{config.get('name', name)} 尚未配置 API Key，请在服务端设置相应环境变量"
        )
    return config


def stream_chat(
    provider_name: str,
    messages: list[dict],
    model: str | None = None,
    think: bool | None = None,
    temperature: float | None = None,
) -> Iterator[dict]:
    config = get_provider(provider_name)
    if config["kind"] == "ollama":
        yield from ollama.stream_chat(
            messages,
            model=model or config["model"],
            think=think,
            temperature=temperature,
        )
        return
    yield from online.stream_chat(
        config,
        messages,
        model=model or config["model"],
        temperature=temperature,
    )


def chat_with_model(
    provider_name: str,
    messages: list[dict],
    model: str | None = None,
    think: bool | None = None,
    temperature: float | None = None,
) -> tuple[str, dict]:
    parts: list[str] = []
    stats: dict = {}
    for event in stream_chat(
        provider_name,
        messages,
        model=model,
        think=think,
        temperature=temperature,
    ):
        if event["type"] == "delta":
            parts.append(event["content"])
        elif event["type"] == "done":
            stats = event.get("stats", {})
    return "".join(parts), stats


def provider_health() -> list[dict]:
    result = []
    for pid, item in PROVIDERS.items():
        if item["kind"] == "ollama":
            health = (
                ollama.health()
                if OLLAMA_ENABLED
                else {"ok": False, "error": "本地 Ollama 已关闭"}
            )
            result.append(
                {
                    "id": pid,
                    "name": item["name"],
                    "kind": "ollama",
                    "configured": item["configured"],
                    "model": item["model"],
                    "ok": bool(health.get("ok")),
                    "error": health.get("error"),
                }
            )
        else:
            result.append(
                {
                    "id": pid,
                    "name": item["name"],
                    "kind": "openai",
                    "configured": item["configured"],
                    "model": item["model"],
                    "ok": item["configured"],
                    "error": None if item["configured"] else "未配置 API Key",
                }
            )
    return result


def list_models() -> list[dict]:
    """给前端模型下拉框使用的模型列表。"""
    items: list[dict] = []
    for pid, item in PROVIDERS.items():
        if item["kind"] == "openai" and item["configured"]:
            items.append(
                {
                    "id": item["model"],
                    "name": item["model"],
                    "provider": pid,
                    "provider_name": item["name"],
                    "online": True,
                    "size": 0,
                }
            )
    if OLLAMA_ENABLED:
        health = ollama.health()
        if health.get("ok"):
            try:
                for model in ollama.list_models():
                    items.append(
                        {
                            **model,
                            "provider": "ollama",
                            "provider_name": "本地 Ollama",
                            "online": False,
                        }
                    )
            except ollama.OllamaError:
                pass
    return items
