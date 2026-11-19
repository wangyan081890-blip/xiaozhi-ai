"""开发代理护栏：路径白名单、密钥检测、文件数量限制。"""
from __future__ import annotations

import pytest

from app import devguard
from app.redact import contains_secret, redact


@pytest.mark.parametrize(
    "path",
    ["app/main.py", "app/dev.py", "static/app.js", "static/style.css", "docs/plan.md", "README.md"],
)
def test_allowed_writes(path):
    assert devguard.is_allowed_write(path)


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_accounts.py",
        ".github/workflows/agent.yml",
        "Dockerfile",
        "docker-compose.yml",
        "requirements.txt",
        "config.env",
        ".env",
        "app/../tests/test_x.py",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "scripts/dev_agent.py",
    ],
)
def test_denied_writes(path):
    assert not devguard.is_allowed_write(path)


def test_read_permissions():
    assert devguard.is_allowed_read("app/main.py")
    assert devguard.is_allowed_read("tests/test_accounts.py")
    assert not devguard.is_allowed_read(".env")
    assert not devguard.is_allowed_read("config.env")
    assert not devguard.is_allowed_read(".git/config")
    assert not devguard.is_allowed_read("app/../../etc/passwd")


def test_content_guards():
    with pytest.raises(ValueError):
        devguard.check_content_size("x" * (devguard.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError):
        devguard.check_file_count([f"app/f{i}.py" for i in range(devguard.MAX_FILES + 1)])


def test_secret_detection():
    assert contains_secret("token=sk-abcdefghijklmnopqrstuvwxyz123456")
    assert contains_secret("postgresql://user:supersecret@host/db")
    assert not contains_secret("print('hello world')")
    masked = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
    assert "abcdefghijklmnopqrstuvwxyz123456" not in masked
