"""断点续传下载器（仅用标准库，Python 自带 OpenSSL，可绕过系统 schannel 限制）。"""
import os
import sys
import time
import urllib.request

TARGETS = [
    (
        "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip",
        "runtime/ollama-windows-amd64.zip",
    ),
    (
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
        "runtime/cloudflared.exe",
    ),
]

CHUNK = 1024 * 1024


def download(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    existing = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if existing:
        headers["Range"] = f"bytes={existing}-"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = resp.headers.get("Content-Length")
        total = int(total) + existing if total else 0
        mode = "ab" if existing else "wb"
        done = existing
        started = time.time()
        last_log = 0.0
        with open(tmp, mode) as f:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last_log >= 3:
                    speed = done / max(now - started, 0.001) / 1024 / 1024
                    pct = f"{done / total * 100:.1f}%" if total else "?"
                    print(f"{os.path.basename(path)} {pct} {done/1024/1024:.0f}/{total/1024/1024:.0f} MB {speed:.2f} MB/s", flush=True)
                    last_log = now

    os.replace(tmp, path)
    print(f"DONE {path} {os.path.getsize(path)/1024/1024:.1f} MB", flush=True)


def main():
    for url, rel in TARGETS:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)
        if os.path.exists(path):
            print(f"SKIP exists {path}", flush=True)
            continue
        print(f"START {url}", flush=True)
        download(url, path)


if __name__ == "__main__":
    main()
