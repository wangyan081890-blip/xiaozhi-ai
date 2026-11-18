"""多用户持久化层：SQLite（本地/测试）与 PostgreSQL（云端）共用。

表结构：
  users     —— 匿名用户（浏览器 Cookie 中的 uid）
  sessions  —— 每个会话归属于一个用户
  messages  —— 聊天消息，按 session 隔离

旧版 SQLite 的 chat_history 表会在首次启动时自动迁移并备份。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine

from app.config import DATABASE_URL, DB_ECHO, MAX_MESSAGES_PER_SESSION

_engine: Optional[Engine] = None

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen", DateTime(timezone=True), nullable=False),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("title", Text, nullable=False, default="新对话"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index("ix_messages_session", messages.c.session_id, messages.c.id)
Index("ix_sessions_user", sessions.c.user_id, sessions.c.updated_at)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        kwargs: dict = {"pool_pre_ping": True, "echo": DB_ECHO, "future": True}
        if DATABASE_URL.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
            # 保证 sqlite 文件所在目录存在
            if ":///" in DATABASE_URL and ":memory:" not in DATABASE_URL:
                raw_path = DATABASE_URL.split(":///", 1)[1]
                if not raw_path.startswith("file:"):
                    Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            # 云端 PG（Neon 等）会回收闲置连接，定期回收客户端连接池
            kwargs["pool_recycle"] = 300
        _engine = create_engine(DATABASE_URL, **kwargs)
    return _engine


def init_db() -> None:
    engine = get_engine()
    metadata.create_all(engine)
    if DATABASE_URL.startswith("sqlite"):
        _migrate_legacy_sqlite(engine)


def _migrate_legacy_sqlite(engine: Engine) -> None:
    """把旧版单用户 chat_history 迁移到多用户结构（只执行一次）。"""
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        if "chat_history" not in tables:
            return
        try:
            legacy = conn.execute(text("SELECT COUNT(*) FROM chat_history")).scalar() or 0
            current = conn.execute(select(func.count()).select_from(messages)).scalar() or 0
        except Exception:  # noqa: BLE001
            return
        if legacy and current == 0:
            now = utcnow()
            rows = conn.execute(
                text(
                    "SELECT session_id, provider, role, content, created_at "
                    "FROM chat_history ORDER BY id ASC"
                )
            ).fetchall()
            legacy_uid = "legacy-local"
            existing_user = conn.execute(select(users.c.id).where(users.c.id == legacy_uid)).first()
            if not existing_user:
                conn.execute(users.insert().values(id=legacy_uid, created_at=now, last_seen=now))
            grouped: dict[str, list] = {}
            for row in rows:
                grouped.setdefault(row[0] or "default", []).append(row)
            for session_id, items in grouped.items():
                title = "旧版会话"
                for item in items:
                    if item[2] == "user" and item[3]:
                        title = str(item[3])[:40]
                        break
                existing = conn.execute(
                    select(sessions.c.id).where(sessions.c.id == session_id)
                ).first()
                if not existing:
                    conn.execute(
                        sessions.insert().values(
                            id=session_id,
                            user_id=legacy_uid,
                            title=title,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                for item in items:
                    conn.execute(
                        messages.insert().values(
                            session_id=session_id,
                            provider=item[1] or "local",
                            role=item[2],
                            content=item[3],
                            created_at=item[4] or now,
                        )
                    )
            conn.execute(text("ALTER TABLE chat_history RENAME TO chat_history_backup"))
        conn.commit()


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------
def touch_user(user_id: str) -> None:
    now = utcnow()
    with get_engine().begin() as conn:
        exists = conn.execute(select(users.c.id).where(users.c.id == user_id)).first()
        if exists:
            conn.execute(users.update().where(users.c.id == user_id).values(last_seen=now))
        else:
            conn.execute(users.insert().values(id=user_id, created_at=now, last_seen=now))


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def new_session_id() -> str:
    return uuid.uuid4().hex


def create_session(user_id: str, title: str = "新对话") -> dict:
    now = utcnow()
    session_id = new_session_id()
    with get_engine().begin() as conn:
        conn.execute(
            sessions.insert().values(
                id=session_id, user_id=user_id, title=title, created_at=now, updated_at=now
            )
        )
    return {"session_id": session_id, "title": title, "created_at": now.isoformat()}


def get_session(session_id: str) -> Optional[dict]:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(
                sessions.c.id,
                sessions.c.user_id,
                sessions.c.title,
                sessions.c.created_at,
                sessions.c.updated_at,
            ).where(sessions.c.id == session_id)
        ).first()
    if not row:
        return None
    return {
        "session_id": row[0],
        "user_id": row[1],
        "title": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


def ensure_session(user_id: str, session_id: str, title: str = "新对话") -> dict:
    """确保会话存在且属于该用户；id 冲突（被别人占用）时抛 ValueError。"""
    existing = get_session(session_id)
    if existing is None:
        now = utcnow()
        with get_engine().begin() as conn:
            try:
                conn.execute(
                    sessions.insert().values(
                        id=session_id,
                        user_id=user_id,
                        title=title,
                        created_at=now,
                        updated_at=now,
                    )
                )
            except Exception:  # noqa: BLE001 - 并发下可能已被创建，回读判定
                pass
        existing = get_session(session_id)
    if existing is None:
        raise ValueError("会话创建失败")
    if existing["user_id"] != user_id:
        raise PermissionError("该会话不属于当前用户")
    return existing


def touch_session(session_id: str, title: Optional[str] = None) -> None:
    values: dict = {"updated_at": utcnow()}
    if title:
        values["title"] = title[:80]
    with get_engine().begin() as conn:
        conn.execute(sessions.update().where(sessions.c.id == session_id).values(**values))


def list_sessions(user_id: str, limit: int = 30) -> list[dict]:
    with get_engine().connect() as conn:
        count_subq = (
            select(messages.c.session_id, func.count().label("messages"))
            .group_by(messages.c.session_id)
            .subquery()
        )
        rows = conn.execute(
            select(
                sessions.c.id,
                sessions.c.title,
                sessions.c.updated_at,
                func.coalesce(count_subq.c.messages, 0),
            )
            .outerjoin(count_subq, count_subq.c.session_id == sessions.c.id)
            .where(sessions.c.user_id == user_id)
            .order_by(sessions.c.updated_at.desc())
            .limit(limit)
        ).fetchall()
    return [
        {
            "session_id": row[0],
            "title": row[1] or "新对话",
            "updated_at": row[2].isoformat() if row[2] else None,
            "messages": int(row[3] or 0),
        }
        for row in rows
    ]


def clear_session(user_id: str, session_id: str) -> int:
    existing = get_session(session_id)
    if not existing or existing["user_id"] != user_id:
        return 0
    with get_engine().begin() as conn:
        conn.execute(messages.delete().where(messages.c.session_id == session_id))
        conn.execute(sessions.delete().where(sessions.c.id == session_id))
    return 1


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------
def save_message(session_id: str, provider: str, role: str, content: str) -> None:
    now = utcnow()
    with get_engine().begin() as conn:
        if role == "user":
            last = conn.execute(
                select(messages.c.role, messages.c.content)
                .where(messages.c.session_id == session_id)
                .order_by(messages.c.id.desc())
                .limit(1)
            ).first()
            if last and last[0] == "user" and last[1] == content:
                return
        total = conn.execute(
            select(func.count())
            .select_from(messages)
            .where(messages.c.session_id == session_id)
        ).scalar() or 0
        if total >= MAX_MESSAGES_PER_SESSION:
            return
        conn.execute(
            messages.insert().values(
                session_id=session_id,
                provider=provider,
                role=role,
                content=content,
                created_at=now,
            )
        )
        values: dict = {"updated_at": now}
        if role == "user":
            current = conn.execute(
                select(sessions.c.title).where(sessions.c.id == session_id)
            ).scalar()
            if current in (None, "", "新对话"):
                values["title"] = content.strip().replace("\n", " ")[:40]
        conn.execute(sessions.update().where(sessions.c.id == session_id).values(**values))


def get_history(session_id: str, limit: int = 50) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(messages.c.provider, messages.c.role, messages.c.content, messages.c.created_at)
            .where(messages.c.session_id == session_id)
            .order_by(messages.c.id.desc())
            .limit(limit)
        ).fetchall()
    return [
        {
            "provider": row[0],
            "role": row[1],
            "content": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }
        for row in reversed(rows)
    ]


def delete_last_assistant(session_id: str) -> int:
    with get_engine().begin() as conn:
        row = conn.execute(
            select(messages.c.id)
            .where(messages.c.session_id == session_id, messages.c.role == "assistant")
            .order_by(messages.c.id.desc())
            .limit(1)
        ).first()
        if not row:
            return 0
        conn.execute(messages.delete().where(messages.c.id == row[0]))
    return 1


def health() -> dict:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "dialect": get_engine().dialect.name}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
