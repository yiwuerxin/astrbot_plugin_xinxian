"""心弦好感度 - 好感度应用服务。

增减、查询、防刷（单事件冷却 + 每日双向限幅）。
所有变化都经这里收口，是好感度数值管理的唯一入口。
"""

from __future__ import annotations

import time
from datetime import date

from ..core.decay import effective_favor
from ..core.decimal import round1
from ..core.events import EventRule
from ..core.identity import is_master as _is_master
from ..core.levels import LevelTable
from ..core.relationship import RelationshipTable
from ..core.models import FavorChange, FavorRecord, LevelDef
from ..storage.base import StorageBackend


class FavorService:
    """好感度门面服务。"""

    def __init__(
        self,
        storage: StorageBackend,
        levels: LevelTable,
        *,
        max_favor: float = 100,
        min_favor: float = -100,
        default_favor: float = 0,
        daily_cap_up: float = 15,
        daily_cap_down: float = 15,
        master_ids: list[str] | tuple[str, ...] = (),
        decay_enabled: bool = False,
        decay_per_day: float = 1.0,
        decay_grace_days: float = 3,
        decay_baseline: float = 0.0,
        relationships: RelationshipTable | None = None,
    ) -> None:
        self._storage = storage
        self._levels = levels
        self.max_favor = max_favor
        self.min_favor = min_favor
        self.default_favor = default_favor
        self.daily_cap_up = daily_cap_up
        self.daily_cap_down = daily_cap_down
        self._master_ids = list(master_ids)
        self._decay = (
            (float(decay_per_day), float(decay_grace_days), float(decay_baseline))
            if decay_enabled
            else None
        )
        self._relationships = relationships
        self._nick_cache: dict[tuple[str, str], str] = {}

    # ---------- 查询 ----------

    def _effective(self, stored: float, updated_at: float) -> float:
        """读取时的有效好感度（启用衰减时按时间向基线靠拢）。"""
        if self._decay is None:
            return round1(stored)
        per_day, grace_days, baseline = self._decay
        return effective_favor(
            stored, updated_at, time.time(),
            per_day=per_day, grace_days=grace_days, baseline=baseline,
        )

    async def get(self, group_id: str, user_id: str) -> FavorRecord:
        """读取记录；无记录时返回默认值（不落库）。返回的是有效好感度（含时间衰减）。"""
        rec = await self._storage.get(group_id, user_id)
        if rec is None:
            rec = FavorRecord(
                group_id=group_id, user_id=user_id,
                favor=self.default_favor, updated_at=0.0,
            )
        else:
            rec.favor = self._effective(rec.favor, rec.updated_at)
        return rec

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        rows = await self._storage.ranking(group_id, limit)
        for r in rows:
            r.favor = self._effective(r.favor, r.updated_at)
        rows.sort(key=lambda r: r.favor, reverse=True)
        return rows

    async def standings(self, group_id: str | None = None, limit: int = 500) -> list[dict]:
        """当前总览：每个成员的有效好感/等级/关系/闲置天数（供 WebUI）。按有效好感降序。"""
        recs = await self._storage.list_favor(group_id, limit)
        now = time.time()
        out: list[dict] = []
        for r in recs:
            eff = self._effective(r.favor, r.updated_at)
            idle = int((now - r.updated_at) // 86400) if r.updated_at > 0 else None
            out.append({
                "group_id": r.group_id,
                "user_id": r.user_id,
                "favor": eff,
                "stored_favor": round1(r.favor),
                "decayed": round1(r.favor) != eff,
                "level": self.level_of(eff).name,
                "relationship": self.relationship_label(r.relationship) if r.relationship else "",
                "nickname": r.nickname or "",
                "updated_at": r.updated_at,
                "idle_days": idle,
            })
        out.sort(key=lambda x: x["favor"], reverse=True)
        return out

    def level_of(self, favor: float) -> LevelDef:
        return self._levels.level_of(favor)

    def is_master(self, user_id: str) -> bool:
        return _is_master(user_id, self._master_ids)

    def relationship_label(self, value: str) -> str:
        """关系值 → 展示名（无关系表或未设置返回原值/空）。"""
        if self._relationships is None:
            return value or ""
        return self._relationships.label_of(value)

    async def set_relationship(self, group_id: str, user_id: str, relationship: str) -> None:
        """设定关系类型标签；无记录时先按默认好感建一条再设（不影响好感数值）。"""
        rec = await self._storage.get(group_id, user_id)
        if rec is None:
            await self._storage.set_value(group_id, user_id, self.default_favor)
        await self._storage.set_relationship(group_id, user_id, (relationship or "").strip())

    async def touch_nickname(self, group_id: str, user_id: str, nickname: str) -> None:
        """更新成员昵称；带内存缓存，昵称未变不写库（不影响好感数值）。"""
        nick = (nickname or "").strip()
        if not nick:
            return
        key = (group_id, user_id)
        if self._nick_cache.get(key) == nick:
            return
        await self._storage.set_nickname(group_id, user_id, nick)
        self._nick_cache[key] = nick

    async def is_first_today(self, group_id: str, user_id: str) -> bool:
        """该成员当日是否还没有互动记录（用于 DAILY_FIRST 事件）。"""
        last = await self._storage.last_event_at(group_id, user_id, "DAILY_FIRST")
        if last is None:
            return True
        return date.fromtimestamp(last) < date.today()

    async def recent_events(
        self, group_id: str, user_id: str, count: int = 3, days: int = 7
    ) -> list[dict]:
        """最近 days 天内、最近 count 条变动流水（倒序），供注入「近期印象」。"""
        if not count or count <= 0:
            return []
        rows = await self._storage.query_logs(group_id, user_id, limit=max(count * 5, count))
        if days and days > 0:
            cutoff = time.time() - days * 86400
            rows = [r for r in rows if r.get("ts", 0) >= cutoff]
        return rows[:count]

    # ---------- 增减 ----------

    async def apply_rules(
        self,
        group_id: str,
        user_id: str,
        rules: list[EventRule],
        source: str = "rule",
    ) -> FavorChange:
        """应用一组命中规则；逐条过冷却与每日上限。"""
        total = FavorChange(delta=0, reason="", source=source)
        reasons: list[str] = []
        for rule in rules:
            ch = await self._apply_one(
                group_id, user_id, rule.delta,
                cooldown_key=rule.event.name,
                cooldown_sec=rule.cooldown_sec,
                reason=rule.event.name, source=source,
            )
            total.delta += ch.delta
            total.clamped = total.clamped or ch.clamped
            total.favor_after = ch.favor_after
            if ch.delta:
                reasons.append(rule.event.name)
        total.reason = ",".join(reasons)
        return total

    async def apply_judge(
        self, group_id: str, user_id: str, delta: float, reason: str = "judge",
        *, message: str = "",
    ) -> FavorChange:
        """应用 LLM 评估结果（judge 的冷却在 JudgeService 里处理）。

        message: 触发本次评估的用户发言原文（截断 200 字入库，供 WebUI 核对是否误判）。
        """
        return await self._apply_one(
            group_id, user_id, delta,
            cooldown_key=None, cooldown_sec=0,
            reason=reason, source="judge",
            message=(message or "")[:200],
        )

    async def change(
        self, group_id: str, user_id: str, delta: float,
        reason: str = "api", source: str = "api",
    ) -> FavorChange:
        """通用增减入口（跨插件 API / 指令使用，无事件冷却，仍受每日限幅）。"""
        return await self._apply_one(
            group_id, user_id, delta,
            cooldown_key=None, cooldown_sec=0,
            reason=reason, source=source,
        )

    async def _apply_one(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        *,
        cooldown_key: str | None,
        cooldown_sec: int,
        reason: str,
        source: str,
        message: str = "",
    ) -> FavorChange:
        now = time.time()
        # 1. 单事件冷却
        if cooldown_key and cooldown_sec > 0:
            last = await self._storage.last_event_at(group_id, user_id, cooldown_key)
            if last is not None and now - last < cooldown_sec:
                rec = await self.get(group_id, user_id)
                return FavorChange(0, reason, source, clamped=True, favor_after=rec.favor)
        # 2. 每日双向限幅
        allowed, capped = await self._cap_by_daily(group_id, user_id, delta)
        if allowed == 0:
            rec = await self.get(group_id, user_id)
            return FavorChange(0, reason, source, clamped=True, favor_after=rec.favor)
        # 3. 落库（锁内原子，含 min_favor..max_favor 封顶 + 1 位小数收敛）
        rec, real = await self._storage.apply_delta(
            group_id, user_id, allowed, self.max_favor, self.min_favor,
            decay=self._decay,
        )
        if real:
            await self._storage.add_daily_gain(
                group_id, user_id, date.today().isoformat(), real
            )
            await self._storage.add_log(
                group_id, user_id, real,
                round1(rec.favor - real), rec.favor,
                reason, source, now, message,
            )
        if cooldown_key:
            await self._storage.touch_event(group_id, user_id, cooldown_key, now)
        return FavorChange(
            real, reason, source,
            clamped=capped or real != allowed, favor_after=rec.favor,
        )

    async def _cap_by_daily(
        self, group_id: str, user_id: str, delta: float
    ) -> tuple[float, bool]:
        """按当日净增量做双向限幅。返回 (限幅后的delta, 是否被截断)。"""
        today = date.today().isoformat()
        # 读时收敛，消除累积小增量的浮点漂移，使比较干净
        gain = round1(await self._storage.daily_gain(group_id, user_id, today))
        if delta > 0:
            used = max(0.0, gain)
            allowed = max(0.0, min(delta, self.daily_cap_up - used))
        else:
            used = max(0.0, -gain)
            allowed = -max(0.0, min(-delta, self.daily_cap_down - used))
        # allowed 也要收敛：daily_cap_up - used 这类减法会引入 IEEE-754 噪声
        # （如 0.3-0.1=0.1999…），不收敛会让 clamped 标志被误判。
        allowed = round1(allowed)
        return allowed, allowed != delta

    # ---------- 管理 ----------

    async def set_favor(
        self, group_id: str, user_id: str, value: float,
        source: str = "admin", reason: str = "set",
    ) -> FavorRecord:
        before = await self.get(group_id, user_id)
        value = round1(max(self.min_favor, min(self.max_favor, float(value))))
        rec = await self._storage.set_value(group_id, user_id, value)
        delta = round1(value - before.favor)
        if delta != 0:
            await self._storage.add_log(
                group_id, user_id, delta, before.favor, value, reason, source, time.time()
            )
        return rec

    async def undo_preview(self, log_id: int) -> dict:
        """预览撤销某条变动的结果（不写库）：返回当前好感与撤销后好感等。"""
        log = await self._storage.get_log(log_id)
        if log is None:
            raise ValueError("记录不存在")
        if log.get("reversed"):
            raise ValueError("该变动已撤销")
        group_id, user_id = log["group_id"], log["user_id"]
        cur = (await self.get(group_id, user_id)).favor
        return {
            "current": cur,
            "after": round1(cur - float(log["delta"])),
            "delta": float(log["delta"]),
            "reason": log.get("reason") or "",
            "message": log.get("message") or "",
        }

    async def undo_log(self, log_id: int) -> FavorRecord:
        """撤销某条变动：反向 delta 落地（标准 undo，不影响之后的其它变动）。

        原流水标记 reversed=1（防重复撤销），并追加一条 source=undo 的反向流水。
        撤销一条 undo 行 = 重做原变动（对称）。
        """
        log = await self._storage.get_log(log_id)
        if log is None:
            raise ValueError("记录不存在")
        if log.get("reversed"):
            raise ValueError("该变动已撤销")
        group_id, user_id = log["group_id"], log["user_id"]
        cur = (await self.get(group_id, user_id)).favor
        target = round1(cur - float(log["delta"]))
        await self.set_favor(
            group_id, user_id, target, source="undo", reason=f"撤销#{log_id}",
        )
        await self._storage.mark_reversed(log_id)
        return await self.get(group_id, user_id)

    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        await self._storage.reset(group_id, user_id)
