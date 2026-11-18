"""SQLite 持久化：按会话保存聊天记录。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "xiaozhi.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                provider TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 兼容旧版本数据库：先补 session_id 列，再建索引（旧表没有该列）
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(chat_history)")}
        if "session_id" not in columns:
            conn.execute(
                "ALTER TABLE chat_history ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'"
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_session ON chat_history(session_id, id)"
        )

        conn.commit()
    finally:
        conn.close()


def save_message(session_id: str, provider: str, role: str, content: str) -> None:
    conn = get_connection()
    try:
        if role == "user":
            # 重试同一条消息时避免重复写入
            last = conn.execute(
                "SELECT role, content FROM chat_history WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if last and last["role"] == "user" and last["content"] == content:
                return
        conn.execute(
            """
            INSERT INTO chat_history (session_id, provider, role, content)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, provider, role, content),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(session_id: str, limit: int = 50) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT provider, role, content, created_at
            FROM chat_history
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in reversed(rows)]


def get_sessions(limit: int = 30) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                session_id,
                COUNT(*) AS messages,
                MAX(created_at) AS updated_at,
                (
                    SELECT content FROM chat_history AS inner_table
                    WHERE inner_table.session_id = outer_table.session_id
                      AND inner_table.role = 'user'
                    ORDER BY inner_table.id ASC
                    LIMIT 1
                ) AS title
            FROM chat_history AS outer_table
            GROUP BY session_id
            ORDER BY MAX(id) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def clear_session(session_id: str) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def delete_last_assistant(session_id: str) -> int:
    """删除该会话最后一条助手回复（“重新生成”时避免重复记录）。"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            DELETE FROM chat_history
            WHERE id = (
                SELECT id FROM chat_history
                WHERE session_id = ? AND role = 'assistant'
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (session_id,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
