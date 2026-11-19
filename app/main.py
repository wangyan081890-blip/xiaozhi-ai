"""小智 AI —— 云端版 FastAPI 入口。

- 默认调用在线大模型（OpenAI 兼容接口），Ollama 仅作开发测试选项；
- 匿名 Cookie 身份 + 多用户会话隔离；
- SQLite / PostgreSQL 均可（DATABASE_URL）；
- SSE 流式输出、限流、并发保护、缓存控制。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import accounts, db, dev, ollama
from app.auth import ensure_user, parse_cookie, set_identity_cookie
from app.config import (
    ADMIN_USERNAMES,
    AGENT_ENABLED,
    APP_NAME,
    APP_VERSION,
    COOKIE_NAME,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_SYSTEM_PROMPT,
    LOGIN_RATE_LIMIT,
    MAX_CONCURRENT,
    MAX_HISTORY_MESSAGES,
    OLLAMA_ENABLED,
    PROVIDERS,
    RATE_LIMIT_PER_MINUTE,
    REGISTRATION_OPEN,
)
from app.providers import ProviderError, chat_with_model, list_models, provider_health, stream_chat
from app.schemas import ChatRequest, DevApproveIn, DevTaskIn, LoginIn, RegisterIn

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}
_slots = threading.BoundedSemaphore(max(1, MAX_CONCURRENT))


log = logging.getLogger("xiaozhi")


def _init_db_with_retry(attempts: int = 6, delay: float = 3.0) -> None:
    """云端数据库可能比应用晚就绪（容器/Neon 冷启动），启动时重试几次。"""
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            db.init_db()
            return
        except Exception as exc:  # noqa: BLE001 - 启动阶段需要兜底
            last_exc = exc
            log.warning("数据库初始化失败（第 %s/%s 次）：%s", i, attempts, exc)
            time.sleep(delay)
    raise RuntimeError(f"数据库初始化失败：{last_exc}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_db_with_retry()
    yield


app = FastAPI(
    title=APP_NAME,
    description="云端运行的小智 AI：在线大模型驱动，多用户会话隔离",
    version=APP_VERSION,
    lifespan=lifespan,
)


class _NullResponse:
    """给 ensure_user 用的占位响应：真正的 Cookie 在中间件末尾设置。"""

    def set_cookie(self, *args, **kwargs) -> None:
        return None


@app.middleware("http")
async def identity_and_cache(request: Request, call_next):
    """分配/识别匿名用户，并禁用页面缓存。"""
    uid = parse_cookie(request.cookies.get(COOKIE_NAME))
    new_user = uid is None
    if new_user:
        uid = ensure_user(request, _NullResponse())
    else:
        request.state.user_id = uid

    response = await call_next(request)

    if new_user:
        # 登录/注册接口已经下发账号 Cookie 时，中间件不要再覆盖成匿名身份。
        already_set = any(
            header.startswith(f"{COOKIE_NAME}=")
            for header in response.headers.getlist("set-cookie")
        )
        if not already_set:
            set_identity_cookie(response, uid, request)
            try:
                db.touch_user(uid)
            except Exception:  # noqa: BLE001 - 数据库故障时不阻塞页面
                pass

    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    elif path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("cf-connecting-ip", "")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def user_id(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if uid:
        return uid
    uid = parse_cookie(request.cookies.get(COOKIE_NAME))
    if uid:
        request.state.user_id = uid
        return uid
    raise HTTPException(status_code=401, detail="身份初始化失败，请刷新页面")


def check_rate_limit(ip: str) -> None:
    if RATE_LIMIT_PER_MINUTE <= 0:
        return
    now = time.time()
    window_start = now - 60
    with _rate_lock:
        bucket = [stamp for stamp in _rate_buckets.get(ip, []) if stamp > window_start]
        if len(bucket) >= RATE_LIMIT_PER_MINUTE:
            _rate_buckets[ip] = bucket
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        bucket.append(now)
        _rate_buckets[ip] = bucket
        if len(_rate_buckets) > 5000:
            for key in [k for k, v in _rate_buckets.items() if not v or v[-1] < window_start]:
                _rate_buckets.pop(key, None)


def normalize_session_id(value: str | None) -> str:
    if value and SESSION_RE.match(value):
        return value
    return db.new_session_id()


def build_messages(request: ChatRequest) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": DEFAULT_SYSTEM_PROMPT}]
    for item in request.messages:
        if item.role == "system":
            continue
        messages.append({"role": item.role, "content": item.content})
    if not any(item["role"] == "user" for item in messages):
        raise HTTPException(status_code=400, detail="消息不能为空")
    return messages[-MAX_HISTORY_MESSAGES - 1 :]


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def resolve_session(uid: str, requested: str | None) -> str:
    sid = normalize_session_id(requested)
    try:
        db.ensure_session(uid, sid)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="无权访问该会话") from exc
    return sid


def resolve_provider(request: ChatRequest) -> str:
    provider = (request.provider or DEFAULT_PROVIDER).strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="不支持的模型服务商")
    if not PROVIDERS[provider].get("configured"):
        raise HTTPException(
            status_code=503,
            detail=f"{PROVIDERS[provider].get('name', provider)} 未配置，请联系管理员设置 API Key",
        )
    return provider


# ---------------------------------------------------------------------------
# 页面与健康检查
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    db_health = db.health()
    ok = bool(db_health.get("ok"))
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "app": APP_NAME, "db": db_health},
    )


# ---------------------------------------------------------------------------
# 状态 / 模型
# ---------------------------------------------------------------------------
@app.get("/api/status")
def status():
    providers = provider_health()
    online_configured = any(p["kind"] == "openai" and p["configured"] for p in providers)
    local_health = (
        {"ok": False, "error": "本地 Ollama 已关闭"} if not OLLAMA_ENABLED else ollama.health()
    )
    models: list[dict] = []
    if OLLAMA_ENABLED and local_health.get("ok"):
        try:
            models = ollama.list_models()
        except ollama.OllamaError:
            models = []
    return {
        "app": APP_NAME,
        "version": app.version,
        "default_provider": DEFAULT_PROVIDER,
        "default_model": DEFAULT_MODEL,
        "online_configured": online_configured,
        "ollama_enabled": OLLAMA_ENABLED,
        "local": local_health,
        "models": models,
        "providers": providers,
        "db": db.health(),
    }


@app.get("/api/models")
def models():
    items = list_models()
    if not items:
        return {
            "ok": False,
            "default_model": DEFAULT_MODEL,
            "models": [],
            "error": "尚未配置任何在线模型 API Key",
        }
    return {"ok": True, "default_model": DEFAULT_MODEL, "models": items}


@app.get("/api/providers")
def providers():
    return {"default": DEFAULT_PROVIDER, "providers": provider_health()}


# ---------------------------------------------------------------------------
# 账号：注册 / 登录 / 登出
# ---------------------------------------------------------------------------
_login_lock = threading.Lock()
_login_buckets: dict[str, list[float]] = {}


def check_login_limit(ip: str) -> None:
    if LOGIN_RATE_LIMIT <= 0:
        return
    now = time.time()
    window_start = now - 60
    with _login_lock:
        bucket = [stamp for stamp in _login_buckets.get(ip, []) if stamp > window_start]
        if len(bucket) >= LOGIN_RATE_LIMIT:
            _login_buckets[ip] = bucket
            raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
        bucket.append(now)
        _login_buckets[ip] = bucket


def current_account(request: Request) -> dict | None:
    row = db.get_user(user_id(request))
    if not row or not row.get("username"):
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"] or "user",
        "is_admin": (row["role"] or "user") == "admin",
    }


def _auth_response(payload: dict, request: Request, user_id_value: str) -> JSONResponse:
    response = JSONResponse(payload)
    set_identity_cookie(response, user_id_value, request)
    return response


@app.get("/api/auth/status")
def auth_status(request: Request):
    account = current_account(request)
    return {
        "authenticated": bool(account),
        "user": account,
        "registration_open": REGISTRATION_OPEN,
        "agent_enabled": AGENT_ENABLED,
        "first_user_admin": db.count_accounts() == 0,
        "admin_usernames": list(ADMIN_USERNAMES),
    }


@app.post("/api/auth/register")
def auth_register(payload: RegisterIn, request: Request):
    check_login_limit(client_ip(request))
    if not REGISTRATION_OPEN:
        raise HTTPException(status_code=403, detail="当前未开放注册，请联系管理员")
    try:
        created = accounts.register(payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _auth_response(
        {
            "ok": True,
            "user": {
                "user_id": created["user_id"],
                "username": created["username"],
                "role": created["role"],
                "is_admin": created["role"] == "admin",
            },
        },
        request,
        created["user_id"],
    )


@app.post("/api/auth/login")
def auth_login(payload: LoginIn, request: Request):
    check_login_limit(client_ip(request))
    account = accounts.login(payload.username, payload.password)
    if not account:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _auth_response(
        {
            "ok": True,
            "user": {
                "user_id": account["user_id"],
                "username": account["username"],
                "role": account["role"],
                "is_admin": account["role"] == "admin",
            },
        },
        request,
        account["user_id"],
    )


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/me")
def me(request: Request):
    uid = user_id(request)
    account = current_account(request)
    return {
        "user_id": uid,
        "authenticated": bool(account),
        "user": account,
        "hint": "登录后可跨浏览器保留账号；未登录时按浏览器匿名隔离",
    }


# ---------------------------------------------------------------------------
# AI 开发系统（仅管理员）
# ---------------------------------------------------------------------------
def require_admin(request: Request) -> dict:
    account = current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    if not account["is_admin"]:
        raise HTTPException(status_code=403, detail="只有管理员可以使用 AI 开发系统")
    return account


@app.get("/api/dev/info")
def dev_info(request: Request):
    require_admin(request)
    return dev.info()


@app.get("/api/dev/tasks")
def dev_tasks(request: Request, limit: int = 30):
    require_admin(request)
    return {"tasks": db.list_dev_tasks(max(1, min(limit, 100)))}


@app.post("/api/dev/tasks")
def dev_create_task(payload: DevTaskIn, request: Request):
    account = require_admin(request)
    try:
        task = dev.create_task(account["user_id"], payload.title, payload.requirement)
    except dev.DevError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.get("/api/dev/tasks/{task_id}")
def dev_task_detail(task_id: str, request: Request):
    require_admin(request)
    task = db.get_dev_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task": task,
        "events": db.list_dev_events(task_id),
        "versions": db.list_dev_versions(20),
    }


@app.post("/api/dev/tasks/{task_id}/cancel")
def dev_cancel_task(task_id: str, request: Request):
    require_admin(request)
    try:
        return {"ok": True, "task": dev.cancel_task(task_id)}
    except dev.DevError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/dev/tasks/{task_id}/approve")
def dev_approve_task(task_id: str, payload: DevApproveIn, request: Request):
    require_admin(request)
    try:
        return {"ok": True, "task": dev.approve(task_id, payload.action, payload.note)}
    except dev.DevError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/dev/tasks/{task_id}/rollback")
def dev_rollback_task(task_id: str, request: Request):
    require_admin(request)
    try:
        return {"ok": True, "task": dev.rollback(task_id)}
    except dev.DevError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/dev/callback")
async def dev_agent_callback(request: Request):
    body = await request.body()
    if not dev.verify_signature(body, request.headers.get("x-dev-signature")):
        raise HTTPException(status_code=401, detail="签名校验失败")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="回调内容不是合法 JSON") from exc
    try:
        dev.handle_agent_callback(payload)
    except dev.DevError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/dev/deploy-callback")
async def dev_deploy_callback(request: Request):
    body = await request.body()
    if not dev.verify_signature(body, request.headers.get("x-dev-signature")):
        raise HTTPException(status_code=401, detail="签名校验失败")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="回调内容不是合法 JSON") from exc
    try:
        dev.handle_deploy_callback(payload)
    except dev.DevError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# 历史记录（按用户隔离）
# ---------------------------------------------------------------------------
@app.get("/api/sessions")
def sessions(request: Request):
    uid = user_id(request)
    rows = db.list_sessions(uid, 50)
    for row in rows:
        title = (row.get("title") or "新对话").replace("\n", " ").strip()
        row["title"] = title[:26] + ("…" if len(title) > 26 else "")
    return {"sessions": rows}


@app.post("/api/sessions")
def create_session(request: Request, payload: dict | None = None):
    uid = user_id(request)
    title = (payload or {}).get("title") if isinstance(payload, dict) else None
    session = db.create_session(uid, title or "新对话")
    return {"ok": True, **session}


@app.get("/api/history")
def history(request: Request, session_id: str = "default", limit: int = 50):
    uid = user_id(request)
    if not SESSION_RE.match(session_id or ""):
        return {"session_id": session_id, "messages": []}
    record = db.get_session(session_id)
    if record is None:
        return {"session_id": session_id, "messages": []}
    if record["user_id"] != uid:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return {
        "session_id": session_id,
        "messages": db.get_history(session_id, max(1, min(limit, 200))),
    }


@app.delete("/api/session")
def delete_session(request: Request, session_id: str = "default"):
    uid = user_id(request)
    if not SESSION_RE.match(session_id or ""):
        return {"ok": True, "deleted": 0}
    deleted = db.clear_session(uid, session_id)
    return {"ok": True, "deleted": deleted}


# ---------------------------------------------------------------------------
# 对话
# ---------------------------------------------------------------------------
def _prepare_chat(request: ChatRequest, http_request: Request) -> tuple[str, str, list[dict]]:
    ip = client_ip(http_request)
    check_rate_limit(ip)
    uid = user_id(http_request)
    provider = resolve_provider(request)
    session_id = resolve_session(uid, request.session_id)
    messages = build_messages(request)

    last_user = next((item["content"] for item in reversed(messages) if item["role"] == "user"), "")
    if request.regenerate:
        db.delete_last_assistant(session_id)
    elif last_user:
        db.save_message(session_id, provider, "user", last_user)
    return provider, session_id, messages


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest, http_request: Request):
    provider, session_id, messages = _prepare_chat(request, http_request)

    if not _slots.acquire(timeout=45):
        raise HTTPException(status_code=429, detail="小智正在回答其他问题，请稍后再试")

    def event_stream():
        answer_parts: list[str] = []
        stats: dict = {}
        try:
            yield sse(
                {
                    "type": "start",
                    "provider": provider,
                    "model": request.model or PROVIDERS[provider]["model"],
                    "session_id": session_id,
                }
            )
            for event in stream_chat(
                provider,
                messages,
                model=request.model,
                think=request.think,
                temperature=request.temperature,
            ):
                if event["type"] == "delta":
                    answer_parts.append(event["content"])
                elif event["type"] == "done":
                    stats = event.get("stats", {})
                yield sse(event)

            answer = "".join(answer_parts).strip()
            if answer:
                db.save_message(session_id, provider, "assistant", answer)
            yield sse({"type": "end", "stats": stats, "length": len(answer)})
        except (ProviderError, ollama.OllamaError) as exc:
            yield sse({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 流式接口必须把错误交给前端
            yield sse({"type": "error", "message": f"生成失败：{exc}"})
        finally:
            _slots.release()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat")
def chat(request: ChatRequest, http_request: Request):
    provider, session_id, messages = _prepare_chat(request, http_request)

    if not _slots.acquire(timeout=45):
        raise HTTPException(status_code=429, detail="小智正在回答其他问题，请稍后再试")
    try:
        answer, stats = chat_with_model(
            provider,
            messages,
            model=request.model,
            think=request.think,
            temperature=request.temperature,
        )
    except (ProviderError, ollama.OllamaError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        _slots.release()

    answer = answer.strip()
    if answer:
        db.save_message(session_id, provider, "assistant", answer)

    return {
        "success": True,
        "provider": provider,
        "session_id": session_id,
        "answer": answer,
        "stats": stats,
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
