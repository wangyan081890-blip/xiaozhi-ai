"""创建 Cloudflare 临时公网隧道，并把分配到的公网网址打开。

前置条件：start.bat 已经在 8000 端口启动网页服务。
用法：python scripts/public_tunnel.py
"""
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED = ROOT / "runtime" / "cloudflared.exe"
LOCAL_URL = "http://127.0.0.1:8000"
URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def server_alive() -> bool:
    try:
        with urllib.request.urlopen(LOCAL_URL + "/healthz", timeout=3) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    if not CLOUDFLARED.exists():
        print("[错误] 找不到 runtime/cloudflared.exe")
        return 1

    if not server_alive():
        print("⚠️  本地网页服务还没有启动。")
        print("   请先双击 start.bat，看到「浏览器即将打开」后再运行本程序。")
        print()

    print("正在创建公网隧道，请稍候（约 5~15 秒）...")
    print("提示：本窗口需要保持打开，关闭后公网网址立即失效。")
    print()

    process = subprocess.Popen(
        [
            str(CLOUDFLARED),
            "tunnel",
            "--no-autoupdate",
            "--url",
            LOCAL_URL,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    found = False
    lock = threading.Lock()

    def reader():
        nonlocal found
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            match = URL_RE.search(line)
            if match and not found:
                with lock:
                    found = True
                url = match.group(0)
                print()
                print("=" * 62)
                print("  ✅ 公网地址已生成（复制给任何人即可访问）：")
                print(f"     {url}")
                print()
                print("  ⚠️  该地址由 Cloudflare 临时分配，重启后会变化。")
                print("  ⚠️  关闭本窗口或电脑休眠后，网站将无法访问。")
                print("=" * 62)
                print()
                try:
                    webbrowser.open(url)
                except Exception:  # noqa: BLE001
                    pass
            elif "ERR" in line or "error" in line.lower():
                print(line)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    try:
        while process.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在关闭隧道...")
        process.terminate()

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()

    return 0


if __name__ == "__main__":
    sys.exit(main())
