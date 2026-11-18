"""把「小智 AI 云端版」部署到 Hugging Face Spaces（Docker），数据库用外部 PostgreSQL（如 Neon）。

用法（在项目根目录）：
    set HF_TOKEN=hf_xxxxxxxx
    set DATABASE_URL=postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require
    set DEEPSEEK_API_KEY=sk-xxxxxxxx
    python scripts/deploy_hf.py

可选变量：
    HF_SPACE=xiaozhi-ai         Space 名称（默认 xiaozhi-ai）
    SECRET_KEY=...              不填则自动生成
    DEFAULT_PROVIDER=deepseek   默认服务商
    HF_PRIVATE=true             私有 Space（默认公开）
    WAIT_SECONDS=900            等待启动的最长秒数

脚本做四件事：
1. 校验参数并规范化 DATABASE_URL（转成 SQLAlchemy + psycopg 的写法）；
2. 创建/复用 Space，上传项目文件（自动排除 .venv/data/测试脚本）；
3. 写入 Space Secrets（数据库、密钥、模型 Key）；
4. 触发重启并轮询 /healthz，成功后打印公网地址。

注意：Neon 的数据库连接串会写入 Space 的 Secret，不会出现在前端或仓库里。
"""

from __future__ import annotations

import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from huggingface_hub import HfApi
    from huggingface_hub.utils import HfHubHTTPError
except ImportError:  # pragma: no cover
    print("缺少依赖：请先执行  pip install huggingface_hub")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent

# 不该上传到 Space 的文件
IGNORE = [
    ".venv/*",
    "venv/*",
    "__pycache__/*",
    "*.pyc",
    "data/*",
    "pgtest/*",
    "tmp*",
    "caddy*",
    "*.zip",
    "*.db",
    "*.db-*",
    "*.log",
    "*.part",
    "config.env",
    ".env",
    "shot-cloud.png",
    "scripts/mock_openai.py",
    "scripts/test_cloud.py",
    "scripts/ui_smoke.mjs",
]


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def normalize_db_url(raw: str) -> str:
    """把 Neon/Heroku 风格连接串转成 SQLAlchemy + psycopg 的写法。"""
    url = raw.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if not url.startswith("postgresql+psycopg://"):
        raise SystemExit("DATABASE_URL 必须是 postgresql:// 或 postgresql+psycopg:// 开头")
    host = url.split("@")[-1].split("/")[0].lower()
    if "sslmode=" not in url and "localhost" not in host and "127.0.0.1" not in host:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def wait_until_up(url: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=15) as r:
                body = r.read().decode("utf-8", "replace")
                if r.status == 200 and '"ok"' in body:
                    return True
                last = f"HTTP {r.status} {body[:120]}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(15)
        print(f"  …等待启动（剩余 {int(deadline - time.time())}s）{last}")
    print(f"最后一次探测：{last}")
    return False


def main() -> int:
    dry = "--dry-run" in sys.argv
    token = env("HF_TOKEN")
    db_url = env("DATABASE_URL")
    deepseek = env("DEEPSEEK_API_KEY")
    space_name = env("HF_SPACE", "xiaozhi-ai")
    provider = env("DEFAULT_PROVIDER", "deepseek")
    secret_key = env("SECRET_KEY") or secrets.token_hex(32)
    private = env("HF_PRIVATE", "").lower() in {"1", "true", "yes"}
    wait_seconds = int(env("WAIT_SECONDS", "900"))

    missing = []
    if not token:
        missing.append("HF_TOKEN")
    if not db_url:
        missing.append("DATABASE_URL")
    if not deepseek:
        missing.append("DEEPSEEK_API_KEY")
    if missing:
        print("缺少环境变量：" + ", ".join(missing))
        print(__doc__)
        return 2

    db_url = normalize_db_url(db_url)
    if dry:
        print("DRY-RUN 参数检查通过")
        print(f"  Space: {space_name}（private={private}）")
        print(f"  DB: {db_url.split('@')[-1]}")
        print(f"  provider: {provider}, secret_key: {secret_key[:6]}…")
        print(f"  上传目录: {ROOT}")
        print(f"  忽略规则: {IGNORE}")
        return 0

    api = HfApi(token=token)
    try:
        user = api.whoami()["name"]
    except HfHubHTTPError as exc:
        print(f"HF_TOKEN 无效或网络不可达：{exc}")
        return 3

    repo_id = f"{user}/{space_name}"
    print(f"准备 Space：{repo_id}")
    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            private=private,
            exist_ok=True,
        )
    except HfHubHTTPError as exc:
        print(f"创建 Space 失败：{exc}")
        return 4

    print("上传项目文件…")
    api.upload_folder(
        repo_id=repo_id,
        repo_type="space",
        folder_path=str(ROOT),
        ignore_patterns=IGNORE,
        commit_message="deploy: 小智 AI 云端版",
    )

    print("写入 Secrets…")
    space_secrets = {
        "DATABASE_URL": db_url,
        "SECRET_KEY": secret_key,
        "DEEPSEEK_API_KEY": deepseek,
        "DEFAULT_PROVIDER": provider,
        "COOKIE_SECURE": "true",
        "OLLAMA_ENABLED": "false",
        "MAX_CONCURRENT": "2",
    }
    for key, value in space_secrets.items():
        api.add_space_secret(repo_id=repo_id, key=key, value=value)

    print("触发重启…")
    api.restart_space(repo_id=repo_id)

    # Space 的访问域名规则：用户名/空间名中的下划线转连字符
    host = f"{user}-{space_name}".lower().replace("_", "-").replace(".", "-")
    url = f"https://{host}.hf.space"
    print(f"等待服务启动：{url}（最多 {wait_seconds}s）")
    if wait_until_up(url, wait_seconds):
        print("\n部署成功，公网地址：")
        print(url)
        print("\n首次打开可能较慢（免费 CPU 冷启动约 30～60 秒）。")
        return 0

    print("\n服务尚未就绪。请打开下面的页面查看构建日志：")
    print(f"https://huggingface.co/spaces/{repo_id}")
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
