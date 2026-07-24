"""心弦好感度 - 好感度应用服务。

增减、查询、防刷（单事件冷却 + 每日双向限幅）。
所有变化都经这里收口，是好感度数值管理的唯一入口。
"""

from __future__ import annotations

import time
from datetime import date

from ..core.events import EventRule
from ..core.identity import is_master as _is_master
from ..core.levels import LevelTable
from ..core.models import FavorChange, FavorRecord, LevelDef
from ..storage.base import StorageBackend


class FavorService:
    """好感度门面服务。"""

    def __init__(
        self,
        storage: StorageBackend,
        levels: LevelTable,
        *,
        max_favor: int = 100,
        default_favor: int = 0,
        daily_cap_up: int = 15,
        daily_cap_down: int = 15,
        master_ids: list[str] | tuple[str, ...] = (),
    ) -> None:
        self._storage = storage
        self._levels = levels
        self.max_favor = max_favor
        self.default_favor = default_favor
        self.daily_cap_up = daily_cap_up
        self.daily_cap_down = daily_cap_down
        self._master_ids = list(master_ids)

    # ---------- 查询 ----------

    async def get(self, group_id: str, user_id: str) -> FavorRecord:
        """读取记录；无记录时返回默认值（不落库）。"""
        rec = await self._storage.get(group_id, user_id)
        if rec is None:
            rec = FavorRecord(
                group_id=group_id, user_id=user_id,
                favor=self.default_favor, updated_at=0.0,
            )
        return rec

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        return await self._storage.ranking(group_id, limit)

    def level_of(self, favor: int) -> LevelDef:
        return self._levels.level_of(favor)

    def is_master(self, user_id: str) -> bool:
        return _is_master(user_id, self._master_ids)

    async def is_first_today(self, group_id: str, user_id: str) -> bool:
        """该成员当日是否还没有互动记录（用于 DAILY_FIRST 事件）。"""
        last = await self._storage.last_event_at(group_id, user_id, "DAILY_FIRST")
        if last is None:
            return True
        return date.fromtimestamp(last) < date.today()

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
        self, group_id: str, user_id: str, delta: int, reason: str = "judge"
    ) -> FavorChange:
        """应用 LLM 评估结果（judge 的冷却在 JudgeService 里处理）。"""
        return await self._apply_one(
            group_id, user_id, delta,
            cooldown_key=None, cooldown_sec=0,
            reason=reason, source="judge",
        )

    async def change(
        self, group_id: str, user_id: str, delta: int,
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
        delta: int,
        *,
        cooldown_key: str | None,
        cooldown_sec: int,
        reason: str,
        source: str,
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
        # 3. 落库（锁内原子，含 0..max 封顶）
        rec, real = await self._storage.apply_delta(group_id, user_id, allowed, self.max_favor)
        if real:
            await self._storage.add_daily_gain(
                group_id, user_id, date.today().isoformat(), real
            )
        if cooldown_key:
            await self._storage.touch_event(group_id, user_id, cooldown_key, now)
        return FavorChange(
            real, reason, source,
            clamped=capped or real != allowed, favor_after=rec.favor,
        )

    async def _cap_by_daily(self, group_id: str, user_id: str, delta: int) -> tuple[int, bool]:
        """按当日净增量做双向限幅。返回 (限幅后的delta, 是否被截断)。"""
        today = date.today().isoformat()
        gain = await self._storage.daily_gain(group_id, user_id, today)
        if delta > 0:
            used = max(0, gain)
            allowed = max(0, min(delta, self.daily_cap_up - used))
        else:
            used = max(0, -gain)
            allowed = -max(0, min(-delta, self.daily_cap_down - used))
        return allowed, allowed != delta

    # ---------- 管理 ----------

    async def set_favor(self, group_id: str, user_id: str, value: int) -> FavorRecord:
        value = max(0, min(self.max_favor, int(value)))
        return await self._storage.set_value(group_id, user_id, value)

    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        await self._storage.reset(group_id, user_id)
