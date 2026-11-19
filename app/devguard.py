"""AI 开发代理的路径与内容护栏（在 Actions 中执行，可被单元测试）。

规则：
- 只允许改 app/、static/、docs/ 下的文件；
- 测试、CI、部署配置、依赖与密钥文件一律只读或禁止；
- 单文件大小与变更文件数量有上限；
- 改动内容不得包含疑似密钥。
"""
from __future__ import annotations

import os
import posixpath

ALLOWED_WRITE_PREFIXES = ("app/", "static/", "docs/")
ALLOWED_WRITE_FILES = ("README.md",)
DENY_WRITE_PREFIXES = (
    ".github/",
    "tests/",
    "scripts/",
    "tools/",
    "runtime/",
    "data/",
)
DENY_WRITE_FILES = {
    "Dockerfile",
    "docker-compose.yml",
    "Caddyfile",
    "render.yaml",
    "railway.json",
    "requirements.txt",
    "requirements-dev.txt",
    "config.env",
    ".env",
    "Modelfile",
}
DENY_READ_PREFIXES = (".git/", "data/", "tmp/", "runtime/", "tools/")
DENY_READ_FILES = {".env", "config.env", ".secret_key", "id_rsa", "id_ed25519"}

MAX_FILES = 8
MAX_FILE_BYTES = 60_000


def safe_path(path: str) -> str:
    """归一化成相对仓库根的 posix 路径；拒绝绝对路径与 .. 穿越。"""
    if not path or not isinstance(path, str):
        raise ValueError("路径为空")
    path = path.replace("\\", "/").strip()
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise ValueError("不允许绝对路径")
    normalized = posixpath.normpath(path)
    if normalized.startswith("..") or normalized == ".":
        raise ValueError("非法路径")
    return normalized


def is_allowed_write(path: str) -> bool:
    try:
        path = safe_path(path)
    except ValueError:
        return False
    if path in DENY_WRITE_FILES or any(path.startswith(prefix) for prefix in DENY_WRITE_PREFIXES):
        return False
    if path in ALLOWED_WRITE_FILES:
        return True
    return any(path.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES)


def is_allowed_read(path: str) -> bool:
    try:
        path = safe_path(path)
    except ValueError:
        return False
    if posixpath.basename(path) in DENY_READ_FILES:
        return False
    return not any(path.startswith(prefix) for prefix in DENY_READ_PREFIXES)


def check_content_size(content: str, limit: int = MAX_FILE_BYTES) -> None:
    if len(content.encode("utf-8")) > limit:
        raise ValueError(f"文件内容过大（上限 {limit} 字节）")


def check_file_count(paths: list[str], limit: int = MAX_FILES) -> None:
    unique = {safe_path(item) for item in paths}
    if len(unique) > limit:
        raise ValueError(f"一次任务最多改动 {limit} 个文件，当前 {len(unique)} 个")


def looks_like_secret(content: str) -> bool:
    from app.redact import contains_secret

    return contains_secret(content)


def repo_files(root: str, limit: int = 400) -> list[str]:
    skip = {".git", "data", "tmp", "runtime", "node_modules", "__pycache__", ".pytest_cache", "tools"}
    result: list[str] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            rel = posixpath.join(posixpath.relpath(base, root).replace("\\", "/"), name)
            rel = rel.lstrip("./")
            if rel.startswith("data/") or rel.startswith("tmp/"):
                continue
            result.append(rel)
            if len(result) >= limit:
                return sorted(result)
    return sorted(result)
