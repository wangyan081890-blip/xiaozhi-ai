"""匿名用户身份：签名 Cookie。

浏览器首次访问时分配一个随机 uid，之后通过 HMAC 签名 Cookie 识别。
同一个浏览器（电脑/手机各自独立）拥有自己的会话与历史，彼此隔离。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid

from fastapi import Request, Response

from app.config import COOKIE_NAME, COOKIE_SECURE, SECRET_KEY, SESSION_MAX_AGE

_UID_RE = re.compile(r"^[0-9a-f]{32}$")


def _sign(uid: str) -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), uid.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def make_cookie(uid: str) -> str:
    return f"{uid}.{_sign(uid)}"


def parse_cookie(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    uid, signature = value.rsplit(".", 1)
    if not _UID_RE.match(uid):
        return None
    if not hmac.compare_digest(signature, _sign(uid)):
        return None
    return uid


def _is_secure(request: Request) -> bool:
    if COOKIE_SECURE == "true":
        return True
    if COOKIE_SECURE == "false":
        return False
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return request.url.scheme == "https" or forwarded == "https"


def set_identity_cookie(response: Response, uid: str, request: Request) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=make_cookie(uid),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        path="/",
    )


def ensure_user(request: Request, response: Response) -> str:
    """返回当前用户 uid；没有有效 Cookie 时创建并写入响应。"""
    uid = parse_cookie(request.cookies.get(COOKIE_NAME))
    if uid:
        request.state.user_id = uid
        return uid
    uid = uuid.uuid4().hex
    set_identity_cookie(response, uid, request)
    request.state.user_id = uid
    return uid
