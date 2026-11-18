"""本地模拟 OpenAI 兼容接口，用于离线自测（不产生真实费用）。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAIL_FIRST = os.getenv("MOCK_FAIL_FIRST", "0") == "1"
_lock = threading.Lock()
_seen = {"count": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[mock] " + fmt % args + "\n")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] not in {"test-key", "sk-test"}:
            body = json.dumps({"error": {"message": "invalid api key"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        with _lock:
            _seen["count"] += 1
            first = _seen["count"] == 1
        if FAIL_FIRST and first:
            body = b'{"error":{"message":"rate limited"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        last_user = ""
        for item in payload.get("messages", []):
            if item.get("role") == "user":
                last_user = item.get("content", "")
        answer = f"（模拟在线模型）收到：{last_user[:40]}。这是流式测试回答。"
        model = payload.get("model", "mock-model")

        if not payload.get("stream"):
            body = json.dumps(
                {
                    "id": "mock",
                    "model": model,
                    "choices": [{"message": {"role": "assistant", "content": answer}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
                ensure_ascii=False,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        pieces = [answer[i : i + 6] for i in range(0, len(answer), 6)]
        for piece in pieces:
            chunk = {
                "id": "mock",
                "model": model,
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.01)
        done = {
            "id": "mock",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        self.wfile.write(f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    port = int(os.getenv("MOCK_PORT", "11436"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    sys.stderr.write(f"[mock] listening on http://127.0.0.1:{port}/v1\n")
    server.serve_forever()
