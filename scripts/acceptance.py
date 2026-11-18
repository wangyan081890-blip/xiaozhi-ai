"""线上验收脚本：对任意已部署的小智 AI 地址跑完整链路检查。

用法：
    python scripts/acceptance.py --base https://xxx.hf.space

检查项：
1. /healthz 正常、数据库连通
2. 首页、静态资源、移动端 viewport
3. /api/status：默认在线模型已配置
4. SSE 流式对话：start → delta* → done → end
5. 历史记录：user/assistant 落库
6. 连续对话：第二轮能带上文
7. 多用户隔离：另一个浏览器看不到会话，越权访问 403
8. Ollama 关闭时的 503 降级

退出码 0 表示全部通过。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, PASS if ok else FAIL, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def open(self, path: str, data: dict | None = None, timeout: int = 120):
        body = None
        headers = {"Accept": "*/*"}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=headers)
        return self.opener.open(req, timeout=timeout)

    def get_json(self, path: str, timeout: int = 30) -> dict:
        with self.open(path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def post_json(self, path: str, data: dict, timeout: int = 60):
        try:
            with self.open(path, data=data, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                return exc.code, {}

    def stream(self, messages: list[dict], session_id: str = "", provider: str = "") -> tuple[list[str], str, str]:
        payload = {"messages": messages, "session_id": session_id, "provider": provider}
        req = urllib.request.Request(
            self.base + "/api/chat/stream",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        events: list[str] = []
        chunks: list[str] = []
        session = session_id
        with self.opener.open(req, timeout=180) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                kind = obj.get("type")
                events.append(kind)
                if kind == "start":
                    session = obj.get("session_id") or session
                elif kind == "delta":
                    chunks.append(obj.get("content", ""))
                elif kind == "done":
                    session = obj.get("session_id") or session
                elif kind == "error":
                    chunks.append(f"[ERROR] {obj.get('message')}")
        return events, "".join(chunks), session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="部署地址，例如 https://xxx.hf.space")
    args = ap.parse_args()

    a = Client(args.base)
    b = Client(args.base)

    # 1. 健康检查
    try:
        health = a.get_json("/healthz")
        check("healthz", bool(health.get("ok")), json.dumps(health, ensure_ascii=False)[:160])
    except Exception as exc:  # noqa: BLE001
        check("healthz", False, f"{type(exc).__name__}: {exc}")
        print("\n服务不可达，后续检查跳过。")
        return 1

    # 2. 页面与静态资源
    with a.open("/", timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
        check("首页", r.status == 200 and "mascot-wrap" in html, f"{len(html)} 字符")
        check("移动端 viewport", "width=device-width" in html)
    with a.open("/static/app.js", timeout=30) as r:
        js = r.read().decode("utf-8", "replace")
        check("前端脚本", r.status == 200 and "__xzBuild" in js, f"{len(js)} 字符")
    with a.open("/static/assets/mascot.png", timeout=30) as r:
        check("萌宠资源", r.status == 200 and int(r.headers.get("Content-Length", "0")) > 1000)

    # 3. 状态
    status = a.get_json("/api/status")
    online = status.get("online_configured")
    check("在线模型已配置", bool(online), f"{status.get('default_provider')} / {status.get('default_model')}")
    check("Ollama 已关闭", status.get("ollama_enabled") is False)

    # 4. 流式对话
    events, answer, sid = a.stream([{"role": "user", "content": "请用一句话介绍你自己"}])
    check("SSE 事件顺序", bool(events) and events[0] == "start" and events[-1] == "end", ",".join(events[:6]))
    check("回答非空", len(answer.strip()) > 0, answer.strip()[:60])

    # 5. 历史
    history = a.get_json(f"/api/history?session_id={sid}")
    msgs = history.get("messages", [])
    check("历史落库", [m.get("role") for m in msgs] == ["user", "assistant"], f"{len(msgs)} 条")

    # 6. 连续对话
    follow = msgs + [{"role": "user", "content": "我刚才问了你什么？"}]
    ev2, ans2, sid2 = a.stream(follow, sid)
    check("连续对话", sid2 == sid and len(ans2.strip()) > 0, ans2.strip()[:60])
    hist2 = a.get_json(f"/api/history?session_id={sid}").get("messages", [])
    check("上下文保留", len(hist2) >= 4, f"{len(hist2)} 条")

    # 7. 多用户隔离
    sessions_b = b.get_json("/api/sessions").get("sessions", [])
    check("用户 B 无会话", len(sessions_b) == 0, f"{len(sessions_b)} 个")
    try:
        b.open(f"/api/history?session_id={sid}", timeout=15).read()
        forbidden = False
    except urllib.error.HTTPError as exc:
        forbidden = exc.code == 403
    check("越权访问被拒", forbidden, f"HTTP {403 if forbidden else '非 403'}")

    # 8. Ollama 降级
    code, _ = a.post_json("/api/chat", {"messages": [{"role": "user", "content": "hi"}], "provider": "ollama"})
    check("Ollama 关闭降级", code == 503, f"HTTP {code}")

    failed = [r for r in results if r[1] == FAIL]
    print()
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项。")
    if failed:
        print("失败项：" + "、".join(name for name, _, _ in failed))
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
