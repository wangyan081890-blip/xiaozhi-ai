"""AI 开发系统的权限与回调签名。"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db, dev
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _register(client: TestClient, admin: bool = False):
    username = f"dev_{uuid.uuid4().hex[:10]}"
    response = client.post("/api/auth/register", json={"username": username, "password": "s3cret-pass"})
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    if admin:
        db.set_user_role(user["user_id"], "admin")
    return user


def test_dev_endpoints_require_admin(client: TestClient):
    _register(client)
    assert client.get("/api/dev/tasks").status_code == 403
    # 请求体校验可能先于权限检查返回 422；两种都不得放行
    assert client.post("/api/dev/tasks", json={"requirement": "实现一个功能"}).status_code in {403, 422}


def test_agent_disabled_blocks_task_creation(client: TestClient):
    _register(client, admin=True)
    info = client.get("/api/dev/info")
    assert info.status_code == 200
    assert info.json()["enabled"] in {True, False}
    if info.json()["enabled"]:
        pytest.skip("当前环境启用了 AGENT_ENABLED，跳过禁用分支")
    response = client.post(
        "/api/dev/tasks",
        json={"title": "测试", "requirement": "给首页加一个演示按钮"},
    )
    assert response.status_code == 400
    assert "未启用" in response.json()["detail"]


def test_callback_requires_signature(client: TestClient):
    payload = json.dumps({"task_id": "x" * 8, "status": "success"}).encode()
    bad = client.post("/api/dev/callback", content=payload, headers={"X-Dev-Signature": "bad"})
    assert bad.status_code == 401


def test_verify_signature_unit(monkeypatch):
    monkeypatch.setattr(dev, "DEV_CALLBACK_SECRET", "unit-secret")
    body = b'{"task_id":"abc"}'
    good = hmac.new(b"unit-secret", body, hashlib.sha256).hexdigest()
    assert dev.verify_signature(body, good)
    assert not dev.verify_signature(body, good[:-1] + "0")


def test_agent_callback_updates_task(client: TestClient, monkeypatch):
    _register(client, admin=True)
    # 直接建任务（绕过派发），验证回调能落库
    task = db.create_dev_task("u-test", "测试任务", "增加一个健康检查说明")
    body = json.dumps(
        {
            "task_id": task["id"],
            "status": "success",
            "plan": "改文档",
            "summary": "新增说明",
            "pr_number": 7,
            "pr_url": "https://github.com/x/y/pull/7",
            "test_summary": "9 passed",
            "files_changed": ["README.md"],
        }
    ).encode()
    monkeypatch.setattr(dev, "DEV_CALLBACK_SECRET", "unit-secret")
    signature = hmac.new(b"unit-secret", body, hashlib.sha256).hexdigest()
    response = client.post("/api/dev/callback", content=body, headers={"X-Dev-Signature": signature})
    assert response.status_code == 200, response.text
    saved = db.get_dev_task(task["id"])
    assert saved["status"] == "awaiting_approval"
    assert saved["pr_number"] == 7
