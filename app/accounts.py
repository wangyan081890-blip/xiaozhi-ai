"""账号与密码：PBKDF2-HMAC-SHA256 加盐哈希 + 用户名校验。

密码只以 pbkdf2_sha256$iterations$salt$hash 形式入库，任何接口都不会返回它；
登录成功与否不区分“用户名不存在”和“密码错误”，避免账号枚举。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from app import db
from app.config import ADMIN_USERNAMES

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 128
ITERATIONS = 310_000


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("用户名需为 3–32 位字母、数字或下划线")
    return username


def validate_password(password: str) -> str:
    if not isinstance(password, str) or not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        raise ValueError(f"密码长度需为 {MIN_PASSWORD}–{MAX_PASSWORD} 个字符")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(stored: str | None, password: str) -> bool:
    if not stored:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def pick_role(username: str) -> str:
    """第一个注册的账号自动成为管理员；ADMIN_USERNAMES 里的用户名优先。"""
    lowered = {name.lower() for name in ADMIN_USERNAMES}
    if username.lower() in lowered:
        return "admin"
    if db.count_accounts() == 0:
        return "admin"
    return "user"


def register(username: str, password: str) -> dict:
    username = validate_username(username)
    password = validate_password(password)
    if db.get_user_by_username(username):
        raise ValueError("该用户名已被注册")
    role = pick_role(username)
    created = db.create_account(username, hash_password(password), role)
    return created


def login(username: str, password: str) -> dict | None:
    username = (username or "").strip()
    if not username or not password:
        return None
    row = db.get_user_by_username(username)
    if not row or not verify_password(row.get("password_hash"), password):
        return None
    db.touch_user(row["user_id"])
    return {"user_id": row["user_id"], "username": row["username"], "role": row["role"] or "user"}


def is_admin(user_id: str) -> bool:
    row = db.get_user(user_id)
    return bool(row and row.get("role") == "admin")
