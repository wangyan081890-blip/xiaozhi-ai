"""账号体系：注册、登录、登出、密码哈希与权限隔离。"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import accounts, db
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _name(prefix: str = "user") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def test_password_hash_roundtrip():
    stored = accounts.hash_password("correct horse battery staple")
    assert stored.startswith("pbkdf2_sha256$")
    assert "correct horse battery staple" not in stored
    assert accounts.verify_password(stored, "correct horse battery staple")
    assert not accounts.verify_password(stored, "wrong password")
    assert not accounts.verify_password("garbage", "x")


def test_register_login_logout(client: TestClient):
    username = _name()
    status = client.get("/api/auth/status").json()
    assert status["registration_open"] is True
    assert status["authenticated"] is False

    response = client.post("/api/auth/register", json={"username": username, "password": "s3cret-pass"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["username"] == username
    assert body["user"]["role"] in {"admin", "user"}
    assert body["user"]["is_admin"] is (body["user"]["role"] == "admin")

    me = client.get("/api/me").json()
    assert me["authenticated"] is True
    assert me["user"]["username"] == username

    # 重复注册被拒绝
    dup = client.post("/api/auth/register", json={"username": username, "password": "s3cret-pass"})
    assert dup.status_code == 400

    # 登出后回到未登录
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is False

    # 错误密码 / 正确密码
    bad = client.post("/api/auth/login", json={"username": username, "password": "wrong-pass"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"username": username, "password": "s3cret-pass"})
    assert ok.status_code == 200
    assert client.get("/api/me").json()["authenticated"] is True


def test_username_is_case_insensitive(client: TestClient):
    username = _name("Case")
    assert client.post("/api/auth/register", json={"username": username, "password": "s3cret-pass"}).status_code == 200
    login = client.post("/api/auth/login", json={"username": username.upper(), "password": "s3cret-pass"})
    assert login.status_code == 200


def test_invalid_registration_payload(client: TestClient):
    assert client.post("/api/auth/register", json={"username": "ab", "password": "s3cret-pass"}).status_code == 422
    assert client.post("/api/auth/register", json={"username": "bad name", "password": "s3cret-pass"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": _name(), "password": "short"}).status_code == 422


def test_session_isolation_between_accounts(client: TestClient):
    user_a, user_b = _name("a"), _name("b")
    client.post("/api/auth/register", json={"username": user_a, "password": "s3cret-pass"})
    created = client.post("/api/sessions", json={"title": "A 的会话"}).json()
    token_a = created["session_id"]

    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"username": user_b, "password": "s3cret-pass"})
    assert client.get("/api/sessions").json()["sessions"] == []
    assert client.get(f"/api/history?session_id={token_a}").status_code == 403
    assert client.delete(f"/api/session?session_id={token_a}").json()["deleted"] == 0


def test_first_account_becomes_admin(client: TestClient):
    if db.count_accounts() > 0:
        pytest.skip("已有账号，无法验证首个账号")
    created = client.post("/api/auth/register", json={"username": _name(), "password": "s3cret-pass"}).json()
    assert created["user"]["role"] == "admin"
