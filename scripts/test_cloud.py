"""云端版自测：在线流式对话、多用户隔离、重启后历史保留。

用法：
  python scripts/test_cloud.py --base http://127.0.0.1:8200 --phase1
  （重启服务）
  python scripts/test_cloud.py --base http://127.0.0.1:8200 --phase2
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8200")
STATE = pathlib.Path(
    os.environ.get(
        "TEST_STATE",
        str(pathlib.Path(__file__).resolve().parent.parent / "data" / "test-state.json"),
    )
)


def opener_with_jar(jar):
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get_json(opener, path):
    try:
        with opener.open(BASE + path, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def post_json(opener, path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def stream_chat(opener, payload):
    req = urllib.request.Request(
        BASE + "/api/chat/stream",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    events = []
    with opener.open(req, timeout=60) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


def phase1():
    jar = http.cookiejar.CookieJar()
    opener = opener_with_jar(jar)
    status, body = get_json(opener, "/api/status")
    assert status == 200, body
    print("STATUS", body["default_provider"], body["default_model"], body["online_configured"])
    assert body["online_configured"] is True, "在线模型未配置"

    _, me = get_json(opener, "/api/me")
    session_id = "testsession" + me["user_id"][:8]
    events = stream_chat(
        opener,
        {
            "messages": [{"role": "user", "content": "你好，请介绍一下你自己"}],
            "session_id": session_id,
        },
    )
    types = [e["type"] for e in events]
    print("EVENTS", types[:6], "...", types[-2:])
    assert types[0] == "start" and "delta" in types and types[-1] == "end"
    answer = "".join(e["content"] for e in events if e["type"] == "delta")
    print("ANSWER", answer)
    assert "模拟在线模型" in answer

    _, history = get_json(opener, f"/api/history?session_id={session_id}")
    roles = [m["role"] for m in history["messages"]]
    print("HISTORY", roles)
    assert roles == ["user", "assistant"], roles

    # 非流式接口
    _, chat = post_json(
        opener,
        "/api/chat",
        {"messages": [{"role": "user", "content": "非流式测试"}], "session_id": session_id},
    )
    assert chat["success"] is True, chat
    print("NONSTREAM", chat["answer"][:20])

    # 第二个用户：看不到第一个用户的会话
    jar_b = http.cookiejar.CookieJar()
    opener_b = opener_with_jar(jar_b)
    _, sessions_b = get_json(opener_b, "/api/sessions")
    assert all(s["session_id"] != session_id for s in sessions_b["sessions"]), sessions_b
    code, _ = get_json(opener_b, f"/api/history?session_id={session_id}")
    assert code == 403, code
    print("ISOLATION ok, userB sessions:", len(sessions_b["sessions"]))

    # 未授权 provider
    code, err = post_json(
        opener,
        "/api/chat",
        {"messages": [{"role": "user", "content": "hi"}], "provider": "ollama"},
    )
    print("OLLAMA-DISABLED", code, err.get("detail"))
    assert code == 503

    cookies = [{"name": c.name, "value": c.value} for c in jar]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"cookies": cookies, "session_id": session_id, "user": me["user_id"]}),
        encoding="utf-8",
    )
    print("PHASE1 OK")


def phase2():
    data = json.loads(STATE.read_text(encoding="utf-8"))
    jar = http.cookiejar.CookieJar()
    for item in data["cookies"]:
        jar.set_cookie(http.cookiejar.Cookie(0, item["name"], item["value"], None, False, "127.0.0.1", False, False, "/", True, False, None, False, None, None, {}))
    opener = opener_with_jar(jar)
    _, me = get_json(opener, "/api/me")
    assert me["user_id"] == data["user"], (me, data["user"])
    _, history = get_json(opener, f"/api/history?session_id={data['session_id']}")
    roles = [m["role"] for m in history["messages"]]
    print("AFTER-RESTART", roles)
    assert roles[:2] == ["user", "assistant"], roles
    _, sessions = get_json(opener, "/api/sessions")
    assert any(s["session_id"] == data["session_id"] for s in sessions["sessions"])
    print("PHASE2 OK: history persisted across restart")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8200")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phase1", action="store_true")
    group.add_argument("--phase2", action="store_true")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    phase1() if args.phase1 else phase2()
