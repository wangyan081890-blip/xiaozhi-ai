"""从 ModelScope 断点续传下载 Qwen3-8B Q4_K_M GGUF（国内 CDN 速度快）。"""
import os
import sys
import time
import urllib.request

URL = "https://modelscope.cn/models/Qwen/Qwen3-8B-GGUF/resolve/master/Qwen3-8B-Q4_K_M.gguf"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "runtime", "models-src", "Qwen3-8B-Q4_K_M.gguf")
TMP = PATH + ".part"
CHUNK = 1024 * 1024


def main():
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    existing = os.path.getsize(TMP) if os.path.exists(TMP) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if existing:
        headers["Range"] = f"bytes={existing}-"

    req = urllib.request.Request(URL, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        total_header = resp.headers.get("Content-Length")
        total = int(total_header) + existing if total_header else 0
        mode = "ab" if existing else "wb"
        done = existing
        started = time.time()
        last_log = 0.0
        with open(TMP, mode) as f:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last_log >= 5:
                    speed = done / max(now - started, 0.001) / 1024 / 1024
                    pct = f"{done / total * 100:.1f}%" if total else "?"
                    print(
                        f"{pct} {done/1024/1024:.0f}/{total/1024/1024:.0f} MB {speed:.2f} MB/s",
                        flush=True,
                    )
                    last_log = now

    os.replace(TMP, PATH)
    print(f"DONE {PATH} {os.path.getsize(PATH)/1024/1024:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
