"""心弦好感度 - user_version 写入辅助。

SQLite 的 PRAGMA 语句不支持参数绑定（占位符会报语法错误），
因此这里用「固定字面量 + 版本分支」写 user_version：
每个分支的 SQL 都是写死的常量字符串，version 经 int 强转校验后
仅用于选择分支，绝不进入 SQL 文本 —— 无任何拼接面。
新增 schema 版本时：在 MIGRATIONS 追加语句的同时，这里同步加一个分支。
"""

from __future__ import annotations

import sqlite3

SUPPORTED_VERSIONS = (1, 2, 3, 4, 5, 6)


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """把 PRAGMA user_version 写为指定版本（仅接受白名单内的整数）。"""
    v = int(version)
    if v == 1:
        conn.execute("PRAGMA user_version(1)")
    elif v == 2:
        conn.execute("PRAGMA user_version(2)")
    elif v == 3:
        conn.execute("PRAGMA user_version(3)")
    elif v == 4:
        conn.execute("PRAGMA user_version(4)")
    elif v == 5:
        conn.execute("PRAGMA user_version(5)")
    elif v == 6:
        conn.execute("PRAGMA user_version(6)")
    else:
        raise ValueError(f"不支持的 schema 版本: {v}")
