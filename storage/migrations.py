"""心弦好感度 - SQLite schema 迁移。

以 PRAGMA user_version 记录结构版本，逐版本顺序升级。
新增版本时在 MIGRATIONS 追加并递增 SCHEMA_VERSION。
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

# 版本号 -> 该版本需要执行的 DDL 语句列表
MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS favor (
            group_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            favor      INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY (group_id, user_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_favor_group ON favor(group_id, favor DESC)",
        """CREATE TABLE IF NOT EXISTS daily_gain (
            group_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            day      TEXT NOT NULL,
            gain     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, user_id, day)
        )""",
        """CREATE TABLE IF NOT EXISTS cooldown (
            group_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            key      TEXT NOT NULL,
            last_ts  REAL NOT NULL,
            PRIMARY KEY (group_id, user_id, key)
        )""",
    ]
}


def migrate(conn: sqlite3.Connection) -> None:
    """把连接指向的数据库升级到最新 schema。"""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    while version < SCHEMA_VERSION:
        version += 1
        for stmt in MIGRATIONS.get(version, []):
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
