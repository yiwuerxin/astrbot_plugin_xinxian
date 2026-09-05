"""心弦好感度 - SQLite 存储后端（默认实现）。

并发策略：check_same_thread=False + 全局 threading.Lock + WAL + busy_timeout。
读-改-写在锁内原子完成。sqlite3 为同步驱动，async 方法内直接执行
（操作均为毫秒级，不阻塞事件循环）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path

from ..core.decay import consolidate_half_life, effective_favor
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
                "SELECT favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points "
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
            half_life=float(row[7] or 10.0), points=row[8] or "[]",
        )

    async def apply_delta(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        max_favor: float,
        min_favor: float = -100.0,
        decay: tuple[float, float, float, float] | None = None,
        default_favor: float = 0.0,
    ) -> tuple[FavorRecord, float]:
        now = time.time()
        with self._lock:
            conn = self._c()
            row = conn.execute(
                "SELECT favor, updated_at, half_life FROM favor WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
            if row:
                current, last_ts, half_life = float(row[0]), row[1], float(row[2] or 10.0)
            else:
                # 无记录以 default_favor 为基数（updated_at=0 表示从未互动，不吃衰减）
                current, last_ts, half_life = float(default_favor or 0.0), 0.0, 10.0
            # 时间衰减（指数遗忘曲线）：落库前先把存量衰减到当下（锁定），再叠加本次增减
            if decay:
                base, growth, h_max, baseline = decay
                current = effective_favor(
                    current, last_ts, now,
                    half_life=half_life, baseline=baseline,
                )
                # 正向互动巩固半衰期（SM-2 式），新 h 随本次写库落列
                new_h = consolidate_half_life(
                    half_life, base=base, growth=growth, h_max=h_max,
                    positive=delta > 0,
                )
            else:
                new_h = half_life
            # 收敛到 1 位小数：吸收每日限幅边界处的浮点幽灵微增量
            new_value = round1(max(min_favor, min(max_favor, current + delta)))
            real_delta = round1(new_value - current)
            conn.execute(
                "INSERT INTO favor(group_id, user_id, favor, updated_at, half_life) VALUES(?,?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "favor=excluded.favor, updated_at=excluded.updated_at, half_life=excluded.half_life",
                (group_id, user_id, new_value, now, new_h),
            )
            conn.commit()
        return FavorRecord(group_id, user_id, new_value, now, half_life=new_h), real_delta

    def _upsert(self, group_id: str, user_id: str, cols: dict[str, object], *, touch: bool = False) -> None:
        """锁内 upsert favor 表的指定列（须在 self._lock 内调用）。

        无记录时 INSERT（一律带 updated_at=now，新行以"现在"起算衰减锚点）；
        行存在时只 UPDATE 给定列。touch=True 时冲突分支一并刷新
        updated_at（好感数值变动必须重置衰减锚点；昵称/关系/印象不算
        好感变动，不重置）。列名来自本类固定调用点，无用户输入。
        """
        now = time.time()
        col_sql = ", ".join(cols)
        ph = ", ".join("?" for _ in cols)
        update_sql = ", ".join(f"{c}=excluded.{c}" for c in cols)
        if touch:
            update_sql += ", updated_at=excluded.updated_at"
        self._c().execute(
            f"INSERT INTO favor(group_id, user_id, updated_at, {col_sql}) "
            f"VALUES(?,?,?,{ph}) "
            f"ON CONFLICT(group_id, user_id) DO UPDATE SET {update_sql}",
            (group_id, user_id, now, *cols.values()),
        )
        self._c().commit()

    async def set_value(self, group_id: str, user_id: str, value: float) -> FavorRecord:
        value = round1(value)
        with self._lock:
            self._upsert(group_id, user_id, {"favor": value}, touch=True)
        return FavorRecord(group_id, user_id, value, time.time())

    async def set_relationship(self, group_id: str, user_id: str, relationship: str) -> None:
        with self._lock:
            self._upsert(group_id, user_id, {"relationship": relationship or ""})

    async def set_nickname(self, group_id: str, user_id: str, nickname: str) -> None:
        with self._lock:
            self._upsert(group_id, user_id, {"nickname": nickname or ""})

    async def set_points(self, group_id: str, user_id: str, points: list[dict]) -> None:
        """写入印象点集（P-C；不改好感数值/衰减锚）。"""
        import json as _json
        with self._lock:
            self._upsert(group_id, user_id, {
                "points": _json.dumps(list(points or []), ensure_ascii=False)})

    async def set_impression(
        self, group_id: str, user_id: str, impression: str, tags: list[str]
    ) -> None:
        with self._lock:
            self._upsert(group_id, user_id, {
                "impression": (impression or "").strip(),
                "tags": json.dumps(list(tags or []), ensure_ascii=False),
                "impression_at": time.time(),
            })

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        # X9：重读走线程池（不再阻塞主线程的回复）
        return await asyncio.to_thread(self._ranking_sync, group_id, limit)

    def _ranking_sync(self, group_id: str, limit: int) -> list[FavorRecord]:
        with self._lock:
            rows = self._c().execute(
                "SELECT user_id, favor, updated_at FROM favor "
                "WHERE group_id=? ORDER BY favor DESC, updated_at ASC LIMIT ?",
                (group_id, limit),
            ).fetchall()
        return [FavorRecord(group_id, r[0], float(r[1]), r[2]) for r in rows]

    async def list_favor(self, group_id: str | None = None, limit: int = 500) -> list[FavorRecord]:
        return await asyncio.to_thread(self._list_favor_sync, group_id, limit)

    def _list_favor_sync(self, group_id: str | None, limit: int) -> list[FavorRecord]:
        with self._lock:
            if group_id:
                rows = self._c().execute(
                    "SELECT user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points FROM favor "
                    "WHERE group_id=? ORDER BY updated_at DESC LIMIT ?",
                    (group_id, limit),
                ).fetchall()
                recs = [
                    FavorRecord(group_id, r[0], float(r[1]), r[2], r[3] or "", r[4] or "",
                                r[5] or "", r[6] or "", r[7] or 0.0, float(r[8] or 10.0),
                                r[9] or "[]")
                    for r in rows
                ]
            else:
                rows = self._c().execute(
                    "SELECT group_id, user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points FROM favor "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                recs = [
                    FavorRecord(r[0], r[1], float(r[2]), r[3], r[4] or "", r[5] or "",
                                r[6] or "", r[7] or "", r[8] or 0.0, float(r[9] or 10.0),
                                r[10] or "[]")
                    for r in rows
                ]
        return recs

    async def distinct_groups(self) -> list[dict]:
        return await asyncio.to_thread(self._distinct_groups_sync)

    def _distinct_groups_sync(self) -> list[dict]:
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
        # 数据最小化边界（生产标准）：message 存触发发言摘录、reason 存评审
        # 理由——两者都可能携带聊天内容，落库前统一截断，兜底所有调用路径
        # （judge 路径在 FavorService 已截，跨插件/管理路径靠这里兜底）。
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
                    (reason or "")[:200],
                    source,
                    ts,
                    (message or "")[:200],
                ),
            )
            self._c().commit()

    async def query_logs(
        self, group_id=None, user_id=None, limit=200, offset=0, fuzzy=False
    ) -> list[dict]:
        # X9：流水查询（面板 limit 可达 1000，LIKE 模糊走不了索引）放线程池
        return await asyncio.to_thread(
            self._query_logs_sync, group_id, user_id, limit, offset, fuzzy)

    def _query_logs_sync(
        self, group_id=None, user_id=None, limit=200, offset=0, fuzzy=False
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
            # X1：默认精确匹配——内部路径（衰减计数/修复期/记忆注入）语义
            # 要求精确，LIKE 前导通配会把互为子串的 QQ 混进来；fuzzy 仅
            # 供 WebUI 搜索显式开启（该路径 LIKE 无法走索引，属已知代价）
            if fuzzy:
                where.append("user_id LIKE ?")
                args.append(f"%{user_id}%")
            else:
                where.append("user_id = ?")
                args.append(user_id)
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

    async def apply_undo(
        self,
        log_id: int,
        *,
        max_favor: float,
        min_favor: float,
        effective=None,
        default_favor: float = 0.0,
    ) -> dict:
        """单事务撤销（契约见 base.py）：校验→反向→流水→标记，一个 commit。"""
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN")
                row = conn.execute(
                    "SELECT group_id, user_id, delta, reversed FROM favor_log WHERE id=?",
                    (int(log_id),),
                ).fetchone()
                if row is None:
                    raise ValueError("记录不存在")
                if row[3]:
                    raise ValueError("该变动已撤销")
                group_id, user_id, delta = row[0], row[1], float(row[2])
                frow = conn.execute(
                    "SELECT favor, updated_at, half_life FROM favor "
                    "WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                if frow:
                    stored, ts, h = float(frow[0]), frow[1], float(frow[2] or 10.0)
                else:
                    stored, ts, h = float(default_favor or 0.0), 0.0, 10.0
                cur = effective(stored, ts, h) if effective else round1(stored)
                target = round1(max(min_favor, min(max_favor, round1(cur - delta))))
                real = round1(target - cur)
                conn.execute(
                    "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                    "favor=excluded.favor, updated_at=excluded.updated_at",
                    (group_id, user_id, target, now),
                )
                if real != 0:
                    conn.execute(
                        "INSERT INTO favor_log(group_id, user_id, delta, favor_before, "
                        "favor_after, reason, source, ts) VALUES(?,?,?,?,?,?,?,?)",
                        (group_id, user_id, real, round1(cur), target,
                         f"撤销#{int(log_id)}", "undo", now),
                    )
                conn.execute(
                    "UPDATE favor_log SET reversed=1 WHERE id=?", (int(log_id),)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "group_id": group_id, "user_id": user_id,
            "before": round1(cur), "after": target, "delta": real,
        }

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
