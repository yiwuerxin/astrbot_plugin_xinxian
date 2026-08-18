"""心弦好感度 - SQLite 存储后端（默认实现）。

并发策略：check_same_thread=False + 全局 threading.Lock + WAL + busy_timeout。
读-改-写在锁内原子完成。sqlite3 为同步驱动，async 方法内直接执行
（操作均为毫秒级，不阻塞事件循环）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from ..core.decay import effective_favor
from ..core.decimal import round1
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
                "SELECT favor, updated_at, relationship, nickname, impression, tags, impression_at "
                "FROM favor WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return FavorRecord(
            group_id=group_id, user_id=user_id,
            favor=float(row[0]), updated_at=row[1],
            relationship=row[2] or "", nickname=row[3] or "",
            impression=row[4] or "", tags=row[5] or "", impression_at=row[6] or 0.0,
        )

    async def apply_delta(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        max_favor: float,
        min_favor: float = -100.0,
        decay: tuple[float, float, float] | None = None,
    ) -> tuple[FavorRecord, float]:
        now = time.time()
        with self._lock:
            conn = self._c()
            row = conn.execute(
                "SELECT favor, updated_at FROM favor WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
            if row:
                current, last_ts = float(row[0]), row[1]
            else:
                current, last_ts = 0.0, 0.0
            # 时间衰减：落库前先把存量衰减到当下（锁定），再叠加本次增减
            if decay:
                per_day, grace_days, baseline = decay
                current = effective_favor(
                    current, last_ts, now,
                    per_day=per_day, grace_days=grace_days, baseline=baseline,
                )
            # 收敛到 1 位小数：吸收每日限幅边界处的浮点幽灵微增量
            new_value = round1(max(min_favor, min(max_favor, current + delta)))
            real_delta = round1(new_value - current)
            conn.execute(
                "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "favor=excluded.favor, updated_at=excluded.updated_at",
                (group_id, user_id, new_value, now),
            )
            conn.commit()
        return FavorRecord(group_id, user_id, new_value, now), real_delta

    async def set_value(self, group_id: str, user_id: str, value: float) -> FavorRecord:
        now = time.time()
        value = round1(value)
        with self._lock:
            self._c().execute(
                "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "favor=excluded.favor, updated_at=excluded.updated_at",
                (group_id, user_id, value, now),
            )
            self._c().commit()
        return FavorRecord(group_id, user_id, value, now)

    async def set_relationship(self, group_id: str, user_id: str, relationship: str) -> None:
        now = time.time()
        with self._lock:
            self._c().execute(
                "INSERT INTO favor(group_id, user_id, updated_at, relationship) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "relationship=excluded.relationship",
                (group_id, user_id, now, relationship or ""),
            )
            self._c().commit()

    async def set_nickname(self, group_id: str, user_id: str, nickname: str) -> None:
        now = time.time()
        with self._lock:
            self._c().execute(
                "INSERT INTO favor(group_id, user_id, updated_at, nickname) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET nickname=excluded.nickname",
                (group_id, user_id, now, nickname or ""),
            )
            self._c().commit()

    async def set_impression(
        self, group_id: str, user_id: str, impression: str, tags: list[str]
    ) -> None:
        now = time.time()
        with self._lock:
            self._c().execute(
                "INSERT INTO favor(group_id, user_id, updated_at, impression, tags, impression_at) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "impression=excluded.impression, tags=excluded.tags, impression_at=excluded.impression_at",
                (group_id, user_id, now,
                 (impression or "").strip(), json.dumps(list(tags or []), ensure_ascii=False), now),
            )
            self._c().commit()

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        with self._lock:
            rows = self._c().execute(
                "SELECT user_id, favor, updated_at FROM favor "
                "WHERE group_id=? ORDER BY favor DESC, updated_at ASC LIMIT ?",
                (group_id, limit),
            ).fetchall()
        return [FavorRecord(group_id, r[0], float(r[1]), r[2]) for r in rows]

    async def list_favor(self, group_id: str | None = None, limit: int = 500) -> list[FavorRecord]:
        with self._lock:
            if group_id:
                rows = self._c().execute(
                    "SELECT user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at FROM favor "
                    "WHERE group_id=? ORDER BY updated_at DESC LIMIT ?",
                    (group_id, limit),
                ).fetchall()
                recs = [
                    FavorRecord(group_id, r[0], float(r[1]), r[2], r[3] or "", r[4] or "",
                                r[5] or "", r[6] or "", r[7] or 0.0)
                    for r in rows
                ]
            else:
                rows = self._c().execute(
                    "SELECT group_id, user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at FROM favor "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                recs = [
                    FavorRecord(r[0], r[1], float(r[2]), r[3], r[4] or "", r[5] or "",
                                r[6] or "", r[7] or "", r[8] or 0.0)
                    for r in rows
                ]
        return recs

    async def distinct_groups(self) -> list[dict]:
        with self._lock:
            rows = self._c().execute(
                "SELECT group_id, COUNT(*) FROM favor GROUP BY group_id ORDER BY group_id"
            ).fetchall()
        return [{"group_id": r[0], "count": r[1]} for r in rows]

    async def daily_gain(self, group_id: str, user_id: str, day: str) -> float:
        with self._lock:
            row = self._c().execute(
                "SELECT gain FROM daily_gain WHERE group_id=? AND user_id=? AND day=?",
                (group_id, user_id, day),
            ).fetchone()
        # 读时收敛：消除累积小增量导致的浮点漂移，使每日限幅比较干净
        return round1(row[0]) if row else 0.0

    async def add_daily_gain(self, group_id: str, user_id: str, day: str, delta: float) -> None:
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
                for table in ("favor", "daily_gain", "cooldown", "favor_log"):
                    conn.execute(f"DELETE FROM {table} WHERE group_id=?", (group_id,))
            else:
                for table in ("favor", "daily_gain", "cooldown", "favor_log"):
                    conn.execute(
                        f"DELETE FROM {table} WHERE group_id=? AND user_id=?",
                        (group_id, user_id),
                    )
            conn.commit()

    async def add_log(
        self, group_id, user_id, delta, favor_before, favor_after, reason, source, ts,
        message: str = "",
    ) -> None:
        with self._lock:
            self._c().execute(
                "INSERT INTO favor_log(group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    group_id,
                    user_id,
                    round1(delta),
                    round1(favor_before),
                    round1(favor_after),
                    reason,
                    source,
                    ts,
                    (message or ""),
                ),
            )
            self._c().commit()

    async def query_logs(
        self, group_id=None, user_id=None, limit=200, offset=0
    ) -> list[dict]:
        sql = (
            "SELECT id, group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message, reversed "
            "FROM favor_log"
        )
        where: list[str] = []
        args: list = []
        if group_id:
            where.append("group_id = ?")
            args.append(group_id)
        if user_id:
            where.append("user_id LIKE ?")
            args.append(f"%{user_id}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        with self._lock:
            rows = self._c().execute(sql, args).fetchall()
        return [
            {
                "id": r[0],
                "group_id": r[1],
                "user_id": r[2],
                "delta": float(r[3]),
                "favor_before": float(r[4]),
                "favor_after": float(r[5]),
                "reason": r[6],
                "source": r[7],
                "ts": r[8],
                "message": r[9] or "",
                "reversed": bool(r[10]),
            }
            for r in rows
        ]

    async def get_log(self, log_id: int) -> dict | None:
        with self._lock:
            row = self._c().execute(
                "SELECT id, group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message, reversed "
                "FROM favor_log WHERE id=?",
                (int(log_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "group_id": row[1],
            "user_id": row[2],
            "delta": float(row[3]),
            "favor_before": float(row[4]),
            "favor_after": float(row[5]),
            "reason": row[6],
            "source": row[7],
            "ts": row[8],
            "message": row[9] or "",
            "reversed": bool(row[10]),
        }

    async def mark_reversed(self, log_id: int) -> None:
        with self._lock:
            self._c().execute(
                "UPDATE favor_log SET reversed=1 WHERE id=?", (int(log_id),)
            )
            self._c().commit()

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
