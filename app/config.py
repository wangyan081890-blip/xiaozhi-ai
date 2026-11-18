"""小智 AI —— 云端版全局配置。

默认使用在线大模型（OpenAI 兼容接口），Ollama 仅作为开发测试选项保留。
所有密钥只从环境变量 / config.env 读取，不会下发到前端。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# config.env（推荐，部署时可整体替换）优先于 .env；已存在的环境变量优先级最高
load_dotenv(BASE_DIR / "config.env")
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value is not None else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


APP_NAME = _env("APP_NAME", "小智 AI")
APP_VERSION = _env("APP_VERSION", "2.0.0")

# ---------------------------------------------------------------------------
# 目录与数据库
# ---------------------------------------------------------------------------
DATA_DIR = Path(_env("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 本地默认 SQLite；云端设置 DATABASE_URL=postgresql+psycopg://user:pass@db:5432/xiaozhi
def _normalize_db_url(url: str) -> str:
    """把托管平台注入的 postgres:// / postgresql:// 转成 SQLAlchemy+psycopg 形式。

    Render、Railway、Heroku 等平台给的是 libpq 风格 URL，SQLAlchemy 2.x 需要
    postgresql+psycopg:// 才能选中已安装的 psycopg 驱动。
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize_db_url(
    _env("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'xiaozhi.db').as_posix()}")
)
DB_ECHO = _env_bool("DB_ECHO", False)

# ---------------------------------------------------------------------------
# 身份与安全
# ---------------------------------------------------------------------------
COOKIE_NAME = _env("COOKIE_NAME", "xz_uid")
SESSION_MAX_AGE = _env_int("SESSION_MAX_AGE", 60 * 60 * 24 * 365)
# auto: 根据请求协议自动判断 Secure（HTTPS 下自动开启）
COOKIE_SECURE = _env("COOKIE_SECURE", "auto").lower()


def _load_secret() -> str:
    configured = _env("SECRET_KEY")
    if configured:
        return configured
    secret_file = DATA_DIR / ".secret_key"
    try:
        if secret_file.exists():
            value = secret_file.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
    except OSError:
        pass
    value = secrets.token_hex(32)
    try:
        secret_file.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return value


SECRET_KEY = _load_secret()

# ---------------------------------------------------------------------------
# 在线大模型（OpenAI 兼容协议）
# ---------------------------------------------------------------------------
ONLINE_TIMEOUT = _env_int("ONLINE_TIMEOUT", 120)
ONLINE_MAX_RETRIES = _env_int("ONLINE_MAX_RETRIES", 2)
ONLINE_TEMPERATURE = _env_float("ONLINE_TEMPERATURE", 0.7)

# ---------------------------------------------------------------------------
# 本地 Ollama（开发测试选项，云端默认关闭）
# ---------------------------------------------------------------------------
OLLAMA_ENABLED = _env_bool("OLLAMA_ENABLED", False)
LOCAL_BASE_URL = _env("LOCAL_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
LOCAL_MODEL = _env("LOCAL_MODEL", "qwen3:8b")
LOCAL_NUM_CTX = _env_int("LOCAL_NUM_CTX", 8192)
LOCAL_TEMPERATURE = _env_float("LOCAL_TEMPERATURE", 0.7)
LOCAL_KEEP_ALIVE = _env("LOCAL_KEEP_ALIVE", "30m")
LOCAL_TIMEOUT = _env_int("LOCAL_TIMEOUT", 300)

# ---------------------------------------------------------------------------
# 业务限制
# ---------------------------------------------------------------------------
MAX_HISTORY_MESSAGES = _env_int("MAX_HISTORY_MESSAGES", 24)
MAX_MESSAGE_CHARS = _env_int("MAX_MESSAGE_CHARS", 4000)
RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 30)
MAX_CONCURRENT = _env_int("MAX_CONCURRENT", 4)
MAX_MESSAGES_PER_SESSION = _env_int("MAX_MESSAGES_PER_SESSION", 4000)

DEFAULT_SYSTEM_PROMPT = _env(
    "SYSTEM_PROMPT",
    "你是「小智」，一个运行在云端、由开源/在线大模型驱动的中文 AI 助手。"
    "你博学、耐心、严谨，用简体中文回答。"
    "对于问题先给出直接结论，再给必要的解释；代码使用 Markdown 代码块并注明语言；"
    "遇到不确定的内容要明确说明，不要编造。",
)


# ---------------------------------------------------------------------------
# 模型服务商注册表
# ---------------------------------------------------------------------------
def _online_provider(
    pid: str,
    name: str,
    key_env: str,
    base_default: str,
    model_default: str,
) -> dict:
    api_key = _env(key_env)
    base_url = _env(f"{pid.upper()}_BASE_URL", base_default).rstrip("/")
    model = _env(f"{pid.upper()}_MODEL", model_default)
    return {
        "id": pid,
        "name": name,
        "kind": "openai",
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "configured": bool(api_key and base_url and model),
    }


PROVIDERS: dict[str, dict] = {
    "deepseek": _online_provider(
        "deepseek", "DeepSeek",
        "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat",
    ),
    "siliconflow": _online_provider(
        "siliconflow", "硅基流动",
        "SILICONFLOW_API_KEY", "https://api.siliconflow.cn/v1", "Qwen/Qwen3-8B",
    ),
    "zhipu": _online_provider(
        "zhipu", "智谱 GLM",
        "ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash",
    ),
    "dashscope": _online_provider(
        "dashscope", "通义千问",
        "DASHSCOPE_API_KEY",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
    ),
    "openai": _online_provider(
        "openai", "OpenAI",
        "OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini",
    ),
}

if _env("CUSTOM_API_KEY") and _env("CUSTOM_BASE_URL") and _env("CUSTOM_MODEL"):
    PROVIDERS["custom"] = {
        "id": "custom",
        "name": "自定义接口",
        "kind": "openai",
        "api_key": _env("CUSTOM_API_KEY"),
        "base_url": _env("CUSTOM_BASE_URL").rstrip("/"),
        "model": _env("CUSTOM_MODEL"),
        "configured": True,
    }

PROVIDERS["ollama"] = {
    "id": "ollama",
    "name": "本地 Ollama（测试）",
    "kind": "ollama",
    "api_key": "",
    "base_url": LOCAL_BASE_URL,
    "model": LOCAL_MODEL,
    "configured": bool(OLLAMA_ENABLED and LOCAL_BASE_URL and LOCAL_MODEL),
}


def _pick_default_provider() -> str:
    requested = _env("DEFAULT_PROVIDER")
    if requested and PROVIDERS.get(requested, {}).get("configured"):
        return requested
    for pid, item in PROVIDERS.items():
        if item["kind"] == "openai" and item["configured"]:
            return pid
    if PROVIDERS["ollama"]["configured"]:
        return "ollama"
    return requested or "deepseek"


DEFAULT_PROVIDER = _pick_default_provider()
DEFAULT_MODEL = PROVIDERS.get(DEFAULT_PROVIDER, {}).get("model", "")
