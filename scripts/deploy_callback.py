"""部署 workflow 的回调：把部署结果签名后发给云端控制面。"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--status", required=True, choices=["success", "failed"])
    parser.add_argument("--error", default="")
    parser.add_argument("--deployment-id", default="")
    args = parser.parse_args()

    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    secret = os.environ.get("DEV_CALLBACK_SECRET", "")
    if not base or not secret:
        print("缺少 APP_BASE_URL / DEV_CALLBACK_SECRET，跳过回调")
        return 0
    body = json.dumps(
        {
            "task_id": args.task_id,
            "status": args.status,
            "error": args.error[:4000],
            "deployment_id": args.deployment_id,
        },
        ensure_ascii=False,
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    for attempt in range(3):
        request = urllib.request.Request(
            f"{base}/api/dev/deploy-callback",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Dev-Signature": signature,
                "User-Agent": "xiaozhi-deploy-workflow",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status < 300:
                    print("部署结果已回调")
                    return 0
        except Exception as exc:  # noqa: BLE001
            print(f"回调失败（第 {attempt + 1} 次）：{exc}")
        time.sleep(5 * (attempt + 1))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
