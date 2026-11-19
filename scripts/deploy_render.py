#!/usr/bin/env python
"""用 Render API 部署小智 AI（可选：先用 GitHub API 建仓并上传代码）。

前置环境变量：
  RENDER_API_KEY     Render 控制台 Account Settings -> API Keys 创建
  DEEPSEEK_API_KEY   https://platform.deepseek.com 创建
  GITHUB_TOKEN       可选；没有现成仓库时用它自动建私有仓库并上传代码
                     （Fine-grained token 需要 Contents: Read and write 与
                      Administration: Read and write，或经典 token 的 repo 权限）

用法：
  python scripts/deploy_render.py --check      # 只校验凭据，不改动任何东西
  python scripts/deploy_render.py --dry-run    # 校验 + 打印将要创建的资源
  python scripts/deploy_render.py              # 真正创建 PG / Web 服务并部署
  python scripts/deploy_render.py --repo-url https://github.com/you/xiaozhi-ai
                                               # 用已有仓库，跳过建仓/上传

注意：
  * 免费 Web 实例无请求 15 分钟后休眠，冷启动约 30-60 秒。
  * 免费 PostgreSQL 实例 30 天后到期（到期前可在 Render 升级或迁到 Neon）。
  * 脚本只做“真实创建”，不会把未上线说成已上线；最后会轮询 /healthz。
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDER_API = "https://api.render.com/v1"
GITHUB_API = "https://api.github.com"

IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "data", "node_modules", "pgtest",
    "screenshots", "tests", "runtime",
}
IGNORE_GLOBS = [
    "tmp*", "caddy*", "*.zip", "*.db", "*.db-*", "*.log", "*.part",
    ".env", "config.env", "*.pyc",
]


def log(msg: str) -> None:
    print(f"[deploy] {msg}", flush=True)


def _request(method: str, url: str, *, headers: dict, body: dict | None = None,
             timeout: int = 60) -> tuple[int, dict | list | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last_exc: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload: dict | list | str = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # 本机链路偶发 TLS UNEXPECTED_EOF / 连接重置，重试即可
            last_exc = exc
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
    reason = getattr(last_exc, "reason", last_exc)
    raise SystemExit(f"网络错误：{url} -> {reason}")


def render(method: str, path: str, body: dict | None = None) -> dict | list | str:
    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        raise SystemExit("缺少 RENDER_API_KEY")
    status, payload = _request(
        method, RENDER_API + path,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        body=body,
    )
    if status >= 300:
        if status == 402:
            raise SystemExit(
                "Render 要求账户先绑定付款方式（免费实例也需要，正常不会扣费）。\n"
                "处理：打开 https://dashboard.render.com/billing 添加银行卡后重新运行本脚本。\n"
                f"原始响应：{payload}"
            )
        raise SystemExit(f"Render API {method} {path} 失败（HTTP {status}）：{payload}")
    return payload


def github(method: str, path: str, body: dict | None = None) -> dict | list:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("缺少 GITHUB_TOKEN（或改用 --repo-url 指向已有仓库）")
    status, payload = _request(
        method, GITHUB_API + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        body=body,
    )
    if status >= 300:
        raise SystemExit(f"GitHub API {method} {path} 失败（HTTP {status}）：{payload}")
    return payload


def _skipped(rel: Path) -> bool:
    for part in rel.parts[:-1]:
        if part in IGNORE_DIRS:
            return True
    name = rel.name
    return any(fnmatch.fnmatch(name, pat) for pat in IGNORE_GLOBS)


def upload_repo(owner: str, repo: str, branch: str = "main") -> None:
    log(f"上传代码到 GitHub 仓库 {owner}/{repo}（分支 {branch}）…")
    files = [p for p in ROOT.rglob("*") if p.is_file()]
    files = [p for p in files if not _skipped(p.relative_to(ROOT))]
    log(f"共 {len(files)} 个文件需要上传")
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        body = {"message": f"deploy: {rel}", "content": content, "branch": branch}
        status, payload = _request(
            "PUT", f"{GITHUB_API}/repos/{owner}/{repo}/contents/{rel}",
            headers={
                "Authorization": f"Bearer {os.environ['GITHUB_TOKEN'].strip()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            body=body,
        )
        if status == 422:  # 文件已存在（auto_init 的 README 等），取 sha 后覆盖
            status2, payload2 = _request(
                "GET", f"{GITHUB_API}/repos/{owner}/{repo}/contents/{rel}?ref={branch}",
                headers={
                    "Authorization": f"Bearer {os.environ['GITHUB_TOKEN'].strip()}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if status2 < 300 and isinstance(payload2, dict) and payload2.get("sha"):
                body["sha"] = payload2["sha"]
                status, payload = _request(
                    "PUT", f"{GITHUB_API}/repos/{owner}/{repo}/contents/{rel}",
                    headers={
                        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN'].strip()}",
                        "Accept": "application/vnd.github+json",
                        "Content-Type": "application/json",
                    },
                    body=body,
                )
        if status >= 300:
            raise SystemExit(f"上传 {rel} 失败（HTTP {status}）：{payload}")
    log("代码上传完成")


def ensure_repo() -> tuple[str, str, str]:
    """返回 (repo_url, owner, repo)。已有仓库时直接解析。"""
    repo_url = os.environ.get("REPO_URL", "").strip()
    if repo_url:
        slug = repo_url.rstrip("/").removesuffix(".git").split("github.com/")[-1]
        owner, _, repo = slug.partition("/")
        if not owner or not repo:
            raise SystemExit(f"无法解析 REPO_URL：{repo_url}")
        return repo_url, owner, repo

    if not os.environ.get("GITHUB_TOKEN", "").strip():
        raise SystemExit("没有现成仓库时请设置 GITHUB_TOKEN，或用 --repo-url 指定已有仓库")

    me = github("GET", "/user")
    assert isinstance(me, dict)
    owner = me["login"]
    repo = os.environ.get("GITHUB_REPO", "xiaozhi-ai")
    log(f"创建 GitHub 仓库 {owner}/{repo} …")
    status, payload = _request(
        "POST", f"{GITHUB_API}/user/repos",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN'].strip()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        body={"name": repo, "private": True, "auto_init": True,
              "description": "小智 AI 云端版（FastAPI + 在线大模型）"},
    )
    if status == 422:  # 已存在
        log("仓库已存在，直接复用")
    elif status >= 300:
        raise SystemExit(f"创建仓库失败（HTTP {status}）：{payload}")
    time.sleep(3)
    upload_repo(owner, repo)
    return f"https://github.com/{owner}/{repo}", owner, repo


def pick_owner() -> str:
    wanted = os.environ.get("RENDER_OWNER_ID", "").strip()
    owners = render("GET", "/owners")
    assert isinstance(owners, list)
    ids = [item["owner"]["id"] for item in owners if item.get("owner")]
    if wanted:
        if wanted not in ids:
            raise SystemExit(f"RENDER_OWNER_ID={wanted} 不在账户下；可选：{ids}")
        return wanted
    if not ids:
        raise SystemExit("当前 API Key 下没有任何 owner")
    return ids[0]


def create_postgres(owner_id: str, region: str) -> tuple[str, str]:
    name = os.environ.get("RENDER_DB_NAME", "xiaozhi-db")
    log(f"创建 Render PostgreSQL（{name}, free, {region}）…")
    payload = render("POST", "/postgres", {
        "name": name,
        "ownerId": owner_id,
        "plan": "free",
        "region": region,
        "databaseName": "xiaozhi",
        "user": "xiaozhi",
        "version": "16",
    })
    assert isinstance(payload, dict)
    pg = payload.get("postgres", payload)
    pg_id = pg.get("id")
    log(f"PostgreSQL id={pg_id}，等待可用…")
    for _ in range(120):
        detail = render("GET", f"/postgres/{pg_id}")
        assert isinstance(detail, dict)
        current = detail.get("postgres", detail)
        status = (current.get("status") or "").lower()
        if status in {"available", "ready"}:
            info = current.get("connectionInfo") or {}
            conn = info.get("internalConnectionString") or info.get("externalConnectionString")
            if conn:
                return pg_id, conn
        if status in {"creation_failed", "failed"}:
            raise SystemExit(f"PostgreSQL 创建失败：{current.get('status')}")
        time.sleep(5)
    raise SystemExit("PostgreSQL 等待超时")


def create_service(owner_id: str, repo_url: str, region: str) -> tuple[str, str]:
    name = os.environ.get("RENDER_SERVICE_NAME", "xiaozhi-ai")
    log(f"创建 Render Web 服务（{name}, docker, free, {region}）…")
    payload = render("POST", "/services", {
        "type": "web_service",
        "name": name,
        "ownerId": owner_id,
        "repo": repo_url,
        "branch": os.environ.get("RENDER_BRANCH", "main"),
        "autoDeploy": "no",
        "serviceDetails": {
            "env": "docker",
            "plan": "free",
            "region": region,
            "healthCheckPath": "/healthz",
            "envSpecificDetails": {
                "dockerfilePath": "./Dockerfile",
                "dockerContext": "./",
            },
        },
    })
    assert isinstance(payload, dict)
    svc = payload.get("service", payload)
    svc_id = svc.get("id")
    log(f"Web 服务 id={svc_id}")
    return svc_id, name


def set_env(svc_id: str, database_url: str) -> None:
    env = [
        {"key": "DATABASE_URL", "value": database_url},
        {"key": "SECRET_KEY", "value": secrets.token_hex(32)},
        {"key": "DEEPSEEK_API_KEY", "value": os.environ["DEEPSEEK_API_KEY"].strip()},
        {"key": "DEFAULT_PROVIDER", "value": os.environ.get("DEFAULT_PROVIDER", "deepseek")},
        {"key": "COOKIE_SECURE", "value": "true"},
        {"key": "OLLAMA_ENABLED", "value": "false"},
        {"key": "MAX_CONCURRENT", "value": "2"},
    ]
    log("写入环境变量…")
    render("PUT", f"/services/{svc_id}/env-vars", env)


def deploy(svc_id: str) -> str:
    log("触发部署…")
    render("POST", f"/services/{svc_id}/deploys", {"clearCache": "do_not_clear"})
    url = ""
    for _ in range(180):
        detail = render("GET", f"/services/{svc_id}")
        assert isinstance(detail, dict)
        svc = detail.get("service", detail)
        details = detail.get("serviceDetails") or {}
        url = svc.get("serviceDetails", {}).get("url") or details.get("url") or url
        deploys = render("GET", f"/services/{svc_id}/deploys?limit=1")
        assert isinstance(deploys, list)
        if deploys:
            d = deploys[0].get("deploy", deploys[0])
            status = (d.get("status") or "").lower()
            log(f"部署状态：{status}")
            if status == "live":
                return url
            if status in {"build_failed", "update_failed", "canceled", "deactivated"}:
                raise SystemExit(f"部署失败：{status}")
        time.sleep(10)
    raise SystemExit(f"部署等待超时；服务地址：{url}")


def wait_health(base_url: str) -> None:
    if not base_url:
        raise SystemExit("服务未返回 URL")
    health = base_url.rstrip("/") + "/healthz"
    log(f"等待健康检查：{health}")
    for _ in range(90):
        try:
            with urllib.request.urlopen(health, timeout=10) as resp:
                if resp.status == 200:
                    log(f"上线成功：{base_url}")
                    return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10)
    raise SystemExit(f"服务已创建但健康检查未通过：{health}")


def main() -> None:
    parser = argparse.ArgumentParser(description="用 Render API 部署小智 AI")
    parser.add_argument("--check", action="store_true", help="只校验凭据")
    parser.add_argument("--dry-run", action="store_true", help="校验并打印将要创建的资源")
    parser.add_argument("--repo-url", default="", help="已有 GitHub 仓库地址（跳过建仓/上传）")
    parser.add_argument("--web-only", action="store_true",
                        help="不创建 Render PostgreSQL，用外部/临时数据库")
    parser.add_argument("--database-url", default="",
                        help="配合 --web-only 使用的 DATABASE_URL（默认容器内 SQLite）")
    args = parser.parse_args()

    if not os.environ.get("RENDER_API_KEY", "").strip():
        raise SystemExit("缺少 RENDER_API_KEY")
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise SystemExit("缺少 DEEPSEEK_API_KEY")
    if args.repo_url:
        os.environ["REPO_URL"] = args.repo_url

    region = os.environ.get("RENDER_REGION", "singapore")
    owner_id = pick_owner()
    log(f"Render owner={owner_id}，region={region}")

    if args.check:
        if os.environ.get("GITHUB_TOKEN", "").strip():
            me = github("GET", "/user")
            assert isinstance(me, dict)
            log(f"GitHub 账号：{me.get('login')}")
        else:
            log("未设置 GITHUB_TOKEN：将要求 --repo-url 指向已有仓库")
        log("凭据校验通过")
        return

    repo_url = os.environ.get("REPO_URL", "").strip()

    if args.dry_run:
        log(f"仓库：{repo_url or '将用 GITHUB_TOKEN 创建 GitHub 私有仓库并上传代码'}")
        if args.web_only:
            log(f"将创建：Web 服务(docker, free, {region})，数据库={args.database_url or '容器内 SQLite'}")
        else:
            log(f"将创建：PostgreSQL(free, {region}) + Web 服务(docker, free, {region})")
        log("dry-run 结束，未改动任何资源")
        return

    if not repo_url:
        repo_url, _, _ = ensure_repo()

    if args.web_only:
        database_url = args.database_url or "sqlite:////app/data/xiaozhi.db"
        pg_id = "none"
        log(f"跳过 Render PostgreSQL；DATABASE_URL={database_url}")
    else:
        pg_id, database_url = create_postgres(owner_id, region)
    svc_id, name = create_service(owner_id, repo_url, region)
    try:
        set_env(svc_id, database_url)
        url = deploy(svc_id)
        wait_health(url)
        print()
        print(f"部署完成：{url}")
        print(f"验收：python scripts\\acceptance.py --base {url}")
        print("提示：免费实例闲置 15 分钟后休眠，首次访问需等待冷启动。")
    except SystemExit:
        print(f"已创建资源：postgres={pg_id} service={svc_id}，可在 Render 控制台继续排查。")
        raise


if __name__ == "__main__":
    main()
