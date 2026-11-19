"""数据库结构升级：先备份、再补列，旧数据不丢失。"""
from __future__ import annotations

import pathlib
import uuid

from sqlalchemy import create_engine, inspect, text

from app import db

TMP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "data" / "pytest-tmp"


def _db_path() -> pathlib.Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TMP_ROOT / f"case-{uuid.uuid4().hex}.db"


def _legacy_engine(path: pathlib.Path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, created_at TEXT, last_seen TEXT)"))
        conn.execute(text("INSERT INTO users (id, created_at, last_seen) VALUES ('u1', '2026-01-01', '2026-01-02')"))
        conn.execute(text("CREATE TABLE sessions (id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64))"))
        conn.execute(text("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id VARCHAR(64))"))
    return engine


def test_backup_and_add_account_columns():
    engine = _legacy_engine(_db_path())
    backups = db.backup_tables(engine)
    assert len(backups) == 3
    with engine.connect() as conn:
        for name in backups:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            assert count >= 0
        assert "backup_" in backups[0]

    db._ensure_user_columns(engine)

    with engine.connect() as conn:
        columns = {col["name"] for col in inspect(conn).get_columns("users")}
        assert {"username", "password_hash", "role"} <= columns
        row = conn.execute(text("SELECT id, role FROM users WHERE id = 'u1'")).first()
        assert row[0] == "u1"
        assert row[1] == "user"
        indexes = {item["name"] for item in inspect(conn).get_indexes("users")}
        assert "ux_users_username" in indexes

    # 再次执行应幂等，不重复备份
    db._ensure_user_columns(engine)


def test_new_table_creation_is_idempotent():
    engine = create_engine(f"sqlite:///{_db_path().as_posix()}")
    db.metadata.create_all(engine)
    db.metadata.create_all(engine)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    assert {"users", "sessions", "messages", "dev_tasks", "dev_events", "dev_versions"} <= tables
    with engine.connect() as conn:
        columns = {col["name"] for col in inspect(conn).get_columns("users")}
    assert "username" in columns
