#!/usr/bin/env python
"""部署前自检：确认仓库内容适合直接交给 Render 构建。

用法：
  python scripts/check_deploy_ready.py

不联网、不改文件，只做静态检查；退出码 0 表示可以上传部署。
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "data", "node_modules", "pgtest",
    "screenshots", "tests", "runtime",
}
IGNORE_GLOBS = ["tmp*", "caddy*", "*.zip", "*.db", "*.db-*", "*.log", "*.part",
                ".env", "config.env", "*.pyc"]
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"rnd_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
]

problems: list[str] = []
notes: list[str] = []


def ok(msg: str) -> None:
    print(f"[ok]   {msg}")


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"[FAIL] {msg}")


def skipped(rel: Path) -> bool:
    for part in rel.parts[:-1]:
        if part in IGNORE_DIRS:
            return True
    return any(fnmatch.fnmatch(rel.name, pat) for pat in IGNORE_GLOBS)


def main() -> int:
    required = ["Dockerfile", "requirements.txt", "render.yaml",
                "app/main.py", "app/config.py", "static/index.html"]
    for item in required:
        if (ROOT / item).exists():
            ok(f"存在 {item}")
        else:
            fail(f"缺少 {item}")

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if "${PORT:-8000}" in dockerfile or "$PORT" in dockerfile:
        ok("Dockerfile 监听 Render 注入的 PORT")
    else:
        fail("Dockerfile 未使用 $PORT（Render 会连不上服务）")
    if "/healthz" in dockerfile:
        ok("Dockerfile 健康检查指向 /healthz")
    else:
        fail("Dockerfile 缺少健康检查")

    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    if "psycopg" in requirements:
        ok("requirements.txt 包含 psycopg")
    else:
        fail("requirements.txt 缺少 psycopg[binary]")

    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    services = blueprint.get("services") or []
    dbs = blueprint.get("databases") or []
    if len(services) == 1 and services[0].get("runtime") == "docker":
        ok("render.yaml：1 个 Docker Web 服务")
        if services[0].get("healthCheckPath") == "/healthz":
            ok("render.yaml：健康检查路径 /healthz")
        else:
            fail("render.yaml：healthCheckPath 不是 /healthz")
    else:
        fail("render.yaml 服务定义不符合预期")
    if len(dbs) == 1:
        ok(f"render.yaml：1 个数据库（{dbs[0].get('name')}）")
    else:
        fail("render.yaml 缺少数据库定义")

    files = [p for p in ROOT.rglob("*") if p.is_file() and not skipped(p.relative_to(ROOT))]
    total = sum(p.stat().st_size for p in files)
    ok(f"待上传文件 {len(files)} 个，约 {total / 1024:.0f} KB")
    if total > 20 * 1024 * 1024:
        notes.append("待上传总量超过 20MB，GitHub 网页上传可能较慢")

    biggest = max(files, key=lambda p: p.stat().st_size)
    if biggest.stat().st_size > 5 * 1024 * 1024:
        fail(f"文件过大：{biggest.relative_to(ROOT)}（{biggest.stat().st_size} B）")

    leaked = []
    for path in files:
        if path.suffix.lower() not in {".py", ".js", ".md", ".yaml", ".yml", ".txt",
                                       ".html", ".css", ".example", ".sh", ".bat"}:
            continue
        rel = path.relative_to(ROOT)
        if rel.name in {".env", "config.env"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                leaked.append(str(rel))
                break
    if leaked:
        fail(f"疑似包含真实密钥，请先移除：{', '.join(sorted(set(leaked)))}")
    else:
        ok("未发现疑似真实密钥（sk-/rnd_/github_pat_/ghp_）")

    for cache in (ROOT / "app" / "__pycache__", ROOT / "scripts" / "__pycache__"):
        if cache.exists():
            notes.append(f"存在缓存目录 {cache.relative_to(ROOT)}（已被忽略，可删）")

    print()
    for note in notes:
        print(f"[note] {note}")
    if problems:
        print(f"自检未通过：{len(problems)} 项问题")
        return 1
    print("自检通过，可以交给 Render 构建。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
