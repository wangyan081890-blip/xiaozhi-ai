"""pytest 公共配置：在导入 app 之前指向独立的测试数据库。"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "pytest"
DATA.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATA_DIR", str(DATA))
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-0123456789abcdef0123456789abcdef")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("OLLAMA_ENABLED", "false")
os.environ.setdefault("DEEPSEEK_API_KEY", "")
os.environ.setdefault("LOGIN_RATE_LIMIT", "100000")
os.environ["DATABASE_URL"] = f"sqlite:///{(DATA / 'test.db').as_posix()}"
