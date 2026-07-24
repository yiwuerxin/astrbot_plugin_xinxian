"""心弦好感度 - SQLite schema 迁移。

以 PRAGMA user_version 记录结构版本，逐版本顺序升级。
新增版本时在 MIGRATIONS 追加并递增 SCHEMA_VERSION。
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 5

# 版本号 -> 该版本需要执行的 DDL/DML 语句列表
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
    ],
    # v2：好感度支持负值 + 一位小数。favor/daily_gain 由 INTEGER 亲和改为 REAL 亲和，
    # 使小数与负值以一致类型存储（INTEGER 亲和虽能存 REAL，但会整数/浮点混存，成为维护陷阱）。
    # 标准原子重建：建新表 → 拷数据 → 删旧表 → 改名 → 重建索引（DROP TABLE 会连索引一起删）。
    # cooldown 无 favor 列，不动。
    2: [
        # favor: INTEGER -> REAL
        """CREATE TABLE IF NOT EXISTS favor_new (
            group_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            favor      REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY (group_id, user_id)
        )""",
        "INSERT INTO favor_new(group_id, user_id, favor, updated_at) "
        "SELECT group_id, user_id, favor, updated_at FROM favor",
        "DROP TABLE favor",
        "ALTER TABLE favor_new RENAME TO favor",
        "CREATE INDEX IF NOT EXISTS idx_favor_group ON favor(group_id, favor DESC)",
        # daily_gain: INTEGER -> REAL
        """CREATE TABLE IF NOT EXISTS daily_gain_new (
            group_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            day      TEXT NOT NULL,
            gain     REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, user_id, day)
        )""",
        "INSERT INTO daily_gain_new(group_id, user_id, day, gain) "
        "SELECT group_id, user_id, day, gain FROM daily_gain",
        "DROP TABLE daily_gain",
        "ALTER TABLE daily_gain_new RENAME TO daily_gain",
    ],
    # v3：好感度变动流水日志（供 WebUI dashboard 展示增减大小与原因）
    3: [
        """CREATE TABLE IF NOT EXISTS favor_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id     TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            delta        REAL NOT NULL,
            favor_before REAL NOT NULL,
            favor_after  REAL NOT NULL,
            reason       TEXT,
            source       TEXT,
            ts           REAL NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_favor_log_group_ts ON favor_log(group_id, ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_favor_log_user ON favor_log(group_id, user_id, ts DESC)",
    ],
    # v4：favor 表加 relationship 列（关系类型标签，与好感数值正交）
    4: [
        "ALTER TABLE favor ADD COLUMN relationship TEXT NOT NULL DEFAULT ''",
    ],
    # v5：favor 表加 nickname 列（发言时捕获昵称，供排行图/WebUI 显示名字）
    5: [
        "ALTER TABLE favor ADD COLUMN nickname TEXT NOT NULL DEFAULT ''",
    ],
}


def migrate(conn: sqlite3.Connection) -> None:
    """把连接指向的数据库升级到最新 schema。

    每个版本独立事务：中途异常自动回滚到该版本起点，user_version 不前移。
    显式 BEGIN 是必需的——默认隔离级别只对 DML 隐式开事务，
    而 v2 首句是 DDL（CREATE），若不在事务内会自动提交、回滚救不回。
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    while version < SCHEMA_VERSION:
        version += 1
        try:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            for stmt in MIGRATIONS.get(version, []):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
