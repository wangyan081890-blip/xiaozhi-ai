# 数据库设计与迁移（PostgreSQL）

小智 AI 云端版通过 `DATABASE_URL` 选择数据库：

- 本地开发：`sqlite:///data/xiaozhi.db`
- 云端：`postgresql+psycopg://xiaozhi:密码@db:5432/xiaozhi`

应用启动时自动建表（SQLAlchemy `create_all`），无需手工执行 DDL；下面的 SQL
等价于应用创建的结构，便于你审查、建只读账号或做迁移。

## 一、表结构（PostgreSQL 等价 DDL）

```sql
CREATE TABLE users (
    id          VARCHAR(64) PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL,
    last_seen   TIMESTAMPTZ NOT NULL
);

CREATE TABLE sessions (
    id          VARCHAR(64) PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL,
    title       TEXT NOT NULL DEFAULT '新对话',
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_sessions_user ON sessions (user_id, updated_at);

CREATE TABLE messages (
    id          SERIAL PRIMARY KEY,
    session_id  VARCHAR(64) NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    provider    VARCHAR(32) NOT NULL,
    role        VARCHAR(16) NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_messages_session ON messages (session_id, id);
```

设计要点：

- `users.id` 是 32 位十六进制随机串（匿名 Cookie 中的 uid），不是自增 ID。
- `sessions.user_id` 冗余保存归属，所有历史读写都带 `user_id` 校验，避免越权。
- `messages.session_id` 外键 + `ON DELETE CASCADE`，删会话即删消息。
- `ix_messages_session (session_id, id)` 支撑「按会话顺序取历史」。
- `ix_sessions_user (user_id, updated_at)` 支撑「我的会话列表按最近使用排序」。
- 时间统一存 UTC（`datetime.now(timezone.utc)`），展示时由前端本地化。

## 二、数据隔离模型

```
users 1 ──< sessions 1 ──< messages
```

- 每个浏览器首次访问得到独立 `users.id`（签名 Cookie）。
- `GET /api/history`、`DELETE /api/session` 都先校验会话归属，跨用户返回 403。
- `GET /api/sessions` 只返回当前 `user_id` 的会话。
- 同一浏览器的多个标签共享身份；换浏览器/清 Cookie 视为新用户。

## 三、从旧版 SQLite 迁移

旧版本地版使用单表 `chat_history(session_id, provider, role, content, created_at)`。
云端版首次连接 SQLite 时会自动迁移：

1. 旧表数据迁入 `users(id='legacy-local')` 名下的会话与消息；
2. 原表重命名为 `chat_history_backup`（不删除，便于核对）。

PostgreSQL 环境不会自动迁移 SQLite 文件。若你希望把旧记录带到云端，按下面步骤：

**步骤 1：导出旧数据（在旧电脑上）**

```bash
sqlite3 xiaozhi.db ".mode insert chat_history" ".output chat_history.sql" "SELECT * FROM chat_history;"
```

**步骤 2：转换成目标表**

旧表没有 user_id/session 元数据，建议整体归到一个「导入用户」：

```sql
-- 在 PostgreSQL 中执行
INSERT INTO users (id, created_at, last_seen)
VALUES ('legacy-local', now(), now())
ON CONFLICT (id) DO NOTHING;

-- 为旧数据里出现过的 session_id 建会话（示例，需逐个替换）
INSERT INTO sessions (id, user_id, title, created_at, updated_at)
VALUES ('旧会话ID', 'legacy-local', '旧对话', now(), now())
ON CONFLICT (id) DO NOTHING;

-- 再按 role/content/created_at 插入 messages
INSERT INTO messages (session_id, provider, role, content, created_at)
VALUES ('旧会话ID', 'legacy', 'user', '……', now());
```

也可以用一段 Python 脚本读取 SQLite（标准库 `sqlite3`）并写入 PostgreSQL
（`psycopg`），逻辑与 `app/db.py` 的 `_migrate_legacy_sqlite` 相同。

## 四、容量与清理

- 每条消息按 1–2 KB 估算；1 万条约 10–20 MB，普通云数据库足够。
- 应用限制：单会话最多 `MAX_MESSAGES_PER_SESSION=4000` 条，超出后丢弃最早消息。
- 会话标题取首条用户消息前 26 字，超长截断。
- 可定期清理 90 天未活跃用户：

```sql
DELETE FROM users WHERE last_seen < now() - interval '90 days';
-- messages 会随 sessions 级联删除（sessions 需先删）
DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE last_seen < now() - interval '90 days');
```

注意顺序：先删 sessions（级联删 messages），再删 users。

## 五、备份与恢复

```bash
# 逻辑备份
docker compose exec -T db pg_dump -U xiaozhi xiaozhi > backup.sql

# 恢复（覆盖）
cat backup.sql | docker compose exec -T db psql -U xiaozhi -d xiaozhi

# 只备份结构
docker compose exec -T db pg_dump -U xiaozhi --schema-only xiaozhi > schema.sql
```

生产建议：每日 `pg_dump` + 异地保存；数据量大时改用 WAL 归档或云数据库自动备份。

## 六、连接与性能

- 驱动：`psycopg[binary]`（SQLAlchemy 方言 `postgresql+psycopg`）。
- 连接池：SQLAlchemy 默认池 + `pool_pre_ping=True`，断线自动重连。
- 单实例 4 并发生成（`MAX_CONCURRENT`）时数据库压力很小；
  如需扩展，把 `uvicorn` 换成多 worker 前先评估限流与内存。
- 数据库不对公网暴露：compose 中 `db` 没有 `ports` 映射，仅容器网络可达。

## 七、SQLite 与 PostgreSQL 差异

| 项目 | SQLite | PostgreSQL |
| --- | --- | --- |
| 自增主键 | INTEGER PRIMARY KEY AUTOINCREMENT | SERIAL / IDENTITY |
| 时间类型 | TEXT/DateTime | TIMESTAMPTZ |
| 并发写 | 全库锁，WAL 缓解 | MVCC，多连接并发 |
| 适用场景 | 本地开发、单机 | 云端多用户 |

应用层已用 SQLAlchemy Core 抹平差异，同一套代码可直接切换。
