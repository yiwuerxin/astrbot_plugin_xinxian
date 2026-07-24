"""心弦好感度 - SQLite 存储后端（默认实现）。

并发策略：check_same_thread=False + 全局 threading.Lock + WAL + busy_timeout。
读-改-写在锁内原子完成。sqlite3 为同步驱动，async 方法内直接执行
（操作均为毫秒级，不阻塞事件循环）。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from ..core.models import FavorRecord
from .base import StorageBackend
from .migrations import migrate


class SQLiteBackend(StorageBackend):
    """基于 sqlite3 标准库的存储后端。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        with self._lock:
            migrate(conn)
        self._conn = conn

    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("storage 尚未 init()")
        return self._conn

    async def get(self, group_id: str, user_id: str) -> FavorRecord | None:
        with self._lock:
            row = self._c().execute(
                "SELECT favor, updated_at FROM favor WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return FavorRecord(group_id=group_id, user_id=user_id, favor=row[0], updated_at=row[1])

    async def apply_delta(
        self, group_id: str, user_id: str, delta: int, max_favor: int
    ) -> tuple[FavorRecord, int]:
        now = time.time()
        with self._lock:
            conn = self._c()
            row = conn.execute(
                "SELECT favor FROM favor WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
            current = row[0] if row else 0
            new_value = max(0, min(max_favor, current + delta))
            real_delta = new_value - current
            conn.execute(
                "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "favor=excluded.favor, updated_at=excluded.updated_at",
                (group_id, user_id, new_value, now),
            )
            conn.commit()
        return FavorRecord(group_id, user_id, new_value, now), real_delta

    async def set_value(self, group_id: str, user_id: str, value: int) -> FavorRecord:
        now = time.time()
        with self._lock:
            self._c().execute(
                "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "favor=excluded.favor, updated_at=excluded.updated_at",
                (group_id, user_id, int(value), now),
            )
            self._c().commit()
        return FavorRecord(group_id, user_id, int(value), now)

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        with self._lock:
            rows = self._c().execute(
                "SELECT user_id, favor, updated_at FROM favor "
                "WHERE group_id=? ORDER BY favor DESC, updated_at ASC LIMIT ?",
                (group_id, limit),
            ).fetchall()
        return [FavorRecord(group_id, r[0], r[1], r[2]) for r in rows]

    async def daily_gain(self, group_id: str, user_id: str, day: str) -> int:
        with self._lock:
            row = self._c().execute(
                "SELECT gain FROM daily_gain WHERE group_id=? AND user_id=? AND day=?",
                (group_id, user_id, day),
            ).fetchone()
        return row[0] if row else 0

    async def add_daily_gain(self, group_id: str, user_id: str, day: str, delta: int) -> None:
        with self._lock:
            self._c().execute(
                "INSERT INTO daily_gain(group_id, user_id, day, gain) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id, day) DO UPDATE SET gain=gain+excluded.gain",
                (group_id, user_id, day, delta),
            )
            self._c().commit()

    async def last_event_at(self, group_id: str, user_id: str, key: str) -> float | None:
        with self._lock:
            row = self._c().execute(
                "SELECT last_ts FROM cooldown WHERE group_id=? AND user_id=? AND key=?",
                (group_id, user_id, key),
            ).fetchone()
        return row[0] if row else None

    async def touch_event(self, group_id: str, user_id: str, key: str, ts: float) -> None:
        with self._lock:
            self._c().execute(
                "INSERT INTO cooldown(group_id, user_id, key, last_ts) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id, key) DO UPDATE SET last_ts=excluded.last_ts",
                (group_id, user_id, key, ts),
            )
            self._c().commit()

    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        with self._lock:
            conn = self._c()
            if user_id is None:
                for table in ("favor", "daily_gain", "cooldown"):
                    conn.execute(f"DELETE FROM {table} WHERE group_id=?", (group_id,))
            else:
                for table in ("favor", "daily_gain", "cooldown"):
                    conn.execute(
                        f"DELETE FROM {table} WHERE group_id=? AND user_id=?",
                        (group_id, user_id),
                    )
            conn.commit()

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
