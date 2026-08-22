"""心弦好感度 - 好感度应用服务。

增减、查询、防刷（单事件冷却 + 每日双向限幅）。
所有变化都经这里收口，是好感度数值管理的唯一入口。
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from ..core.decay import HALF_LIFE_MIN, effective_favor
from ..core.decimal import round1
from ..core.identity import is_master as _is_master
from ..core.impression import build_summary_prompt, parse_summary, stats_tags
from ..core.levels import LevelTable
from ..core.level_economy import EconomyConfig, apply as apply_economy
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
        half_life_base: float = 10.0,
        half_life_growth: float = 1.3,
        half_life_max: float = 60.0,
        decay_baseline: float = 0.0,
        relationships: RelationshipTable | None = None,
        economy: EconomyConfig | None = None,
        impression_interval: int = 8,
        tz_name: str = "",
    ) -> None:
        self._storage = storage
        self._levels = levels
        self.max_favor = max_favor
        self.min_favor = min_favor
        self.default_favor = default_favor
        # 配置钳制：负上限 / <1 的巩固系数会让限幅与遗忘方向反着来，这里兜住
        self.daily_cap_up = max(0.0, float(daily_cap_up))
        self.daily_cap_down = max(0.0, float(daily_cap_down))
        self._master_ids = list(master_ids)
        # 指数遗忘衰减参数：(半衰期基准, 巩固增长系数, 半衰期上限, 基线)。
        # 基准/上限不低于 HALF_LIFE_MIN 保险丝，巩固系数不小于 1（否则正互动
        # 反而缩短半衰期）；上限不小于基准。
        base = max(HALF_LIFE_MIN, float(half_life_base))
        growth = max(1.0, float(half_life_growth))
        h_max = max(base, float(half_life_max))
        self._decay = (
            (base, growth, h_max, float(decay_baseline))
            if decay_enabled
            else None
        )
        self._relationships = relationships
        self._economy = economy
        self._impression_interval = max(1, int(impression_interval))
        self._summarizer = None  # 由 main.py 注入（JudgeService，借其 provider 解析）
        self._nick_cache: dict[tuple[str, str], str] = {}
        # 每日限幅/同日衰减的"一天"边界时区。默认东八区（插件面向 QQ/中文
        # 社区，而 Docker 容器系统时区多为 UTC——按 UTC 换日会让"每天"在
        # 北京时间早 8 点才开始）；显式配置可覆盖为任意 IANA 时区名。
        self._tz = None
        try:
            import zoneinfo

            self._tz = zoneinfo.ZoneInfo(
                (tz_name or "").strip() or "Asia/Shanghai"
            )
        except Exception:
            self._tz = None
        # 后台印象刷新任务持引用（裸 create_task 可能被 GC 中途回收）
        self._bg_tasks: set[asyncio.Task] = set()

    # ---------- 查询 ----------

    def _today(self, now: float | None = None) -> date:
        """配置时区下的"今天"（每日限幅/同日衰减的边界）。"""
        t = time.time() if now is None else now
        dt = datetime.fromtimestamp(t, self._tz) if self._tz else datetime.fromtimestamp(t)
        return dt.date()

    def _day_start(self, now: float | None = None) -> float:
        """配置时区下今天 0 点的 epoch 秒。"""
        d = self._today(now)
        dt = datetime(d.year, d.month, d.day, tzinfo=self._tz) if self._tz else datetime(d.year, d.month, d.day)
        return dt.timestamp()

    def _effective(self, stored: float, updated_at: float, half_life: float = 10.0) -> float:
        """读取时的有效好感度（启用衰减时按指数遗忘曲线向基线收敛）。

        half_life 取自该成员记录（正互动巩固过的老朋友衰减更慢）。
        """
        if self._decay is None:
            return round1(stored)
        base, _growth, _h_max, baseline = self._decay
        return effective_favor(
            stored, updated_at, time.time(),
            half_life=(half_life if half_life and half_life > 0 else base),
            baseline=baseline,
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
            rec.favor = self._effective(rec.favor, rec.updated_at, rec.half_life)
        return rec

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        rows = await self._storage.ranking(group_id, limit)
        for r in rows:
            r.favor = self._effective(r.favor, r.updated_at, getattr(r, "half_life", 10.0))
        rows.sort(key=lambda r: r.favor, reverse=True)
        return rows

    async def standings(self, group_id: str | None = None, limit: int = 500) -> list[dict]:
        """当前总览：每个成员的有效好感/等级/关系/闲置天数（供 WebUI）。按有效好感降序。"""
        recs = await self._storage.list_favor(group_id, limit)
        now = time.time()
        out: list[dict] = []
        for r in recs:
            eff = self._effective(r.favor, r.updated_at, r.half_life)
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
                "impression": r.impression or "",
                "tags": r.parsed_tags(),
                "impression_at": r.impression_at,
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
        """更新成员昵称；带内存缓存，昵称未变不写库（不影响好感数值）。

        无记录时先按 default_favor 建行（否则 INSERT 落列默认 0，
        配置了非零初始好感的新成员会被顶成 0）。"""
        nick = (nickname or "").strip()
        if not nick:
            return
        key = (group_id, user_id)
        if self._nick_cache.get(key) == nick:
            return
        if await self._storage.get(group_id, user_id) is None:
            await self._storage.set_value(group_id, user_id, self.default_favor)
        await self._storage.set_nickname(group_id, user_id, nick)
        self._nick_cache[key] = nick

    async def recent_events(
        self, group_id: str, user_id: str, count: int = 3, days: int = 7,
        *, sig_threshold: float = 0.0, sig_window_mult: float = 1.0,
    ) -> list[dict]:
        """最近 count 条变动流水（倒序），供注入「近期印象」。

        显著性加权（MemoryBank 式）：|delta| ≥ sig_threshold 的重要事件，
        记忆窗口放大 sig_window_mult 倍——大事记得久，小寒暄照常淡忘。
        已撤销（reversed）的变动不作为记忆注入。
        """
        if not count or count <= 0:
            return []
        rows = await self._storage.query_logs(group_id, user_id, limit=max(count * 5, count))
        rows = [r for r in rows if not r.get("reversed")]
        if days and days > 0:
            now = time.time()
            normal_cutoff = now - days * 86400
            sig_cutoff = now - days * 86400 * max(1.0, sig_window_mult)

            def _in_window(r: dict) -> bool:
                if sig_threshold > 0 and abs(float(r.get("delta") or 0)) >= sig_threshold:
                    return r.get("ts", 0) >= sig_cutoff
                return r.get("ts", 0) >= normal_cutoff

            rows = [r for r in rows if _in_window(r)]
        return rows[:count]

    def bind_summarizer(self, judge) -> None:
        """注入 JudgeService（借其 provider 解析与人格名，用于印象汇总调用）。"""
        self._summarizer = judge

    async def maybe_refresh_impression(self, group_id: str, user_id: str, umo: str = "") -> None:
        """每次有效评审后调用：每 interval 次触发一次后台印象刷新（不阻塞、失败静默）。

        umo: 会话标识（用于解析当前人格名）；后台任务持引用防 GC。"""
        if self._summarizer is None or self._impression_interval <= 0:
            return
        try:
            logs = await self._storage.query_logs(group_id, user_id, limit=self._impression_interval)
            hit = sum(1 for r in logs if r.get("source") == "judge")
            if hit and hit % self._impression_interval == 0:
                task = asyncio.create_task(
                    self._refresh_impression_inner(group_id, user_id, umo)
                )
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
        except Exception:
            pass  # 印象是增值功能，任何失败都不影响主链路

    async def refresh_impression_now(self, group_id: str, user_id: str, umo: str = "") -> str:
        """立即刷新印象（指令/WebUI 手动触发），返回一句话结果。"""
        if self._summarizer is None:
            return "印象功能未启用"
        return await self._refresh_impression_inner(group_id, user_id, umo)

    async def _refresh_impression_inner(self, group_id: str, user_id: str, umo: str = "") -> str:
        """收集流水 → 构造汇总提示 → 调 provider → 解析落库。"""
        from astrbot.api import logger  # 延迟导入，保持 core 可测性

        try:
            rec = await self.get(group_id, user_id)
            logs = await self._storage.query_logs(group_id, user_id, limit=20)
            judged = [r for r in logs if r.get("source") == "judge"]
            if not judged:
                return "暂无评估记录，无法生成印象"
            samples = [
                f"{float(r.get('delta') or 0):+} {r.get('message') or ''} —— {r.get('reason') or ''}"
                for r in reversed(judged)  # 时间正序（query_logs 是倒序）
            ]
            # 人格名与 provider 走 JudgeService 公共方法（解析当前会话人格，
            # 失败回落 bot_name），与评审链路同源
            persona_name = await self._summarizer.resolve_display_name(umo)
            prompt = build_summary_prompt(
                rec.nickname or user_id, rec.impression, samples, persona_name
            )
            provider = await self._summarizer.resolve_summary_provider()
            if provider is None:
                return "模型不可用，稍后再试"
            resp = await provider.text_chat(prompt=prompt)
            content = (getattr(resp, "completion_text", "") or "").strip()
            parsed = parse_summary(content)
            if parsed is None:
                return "模型输出无法解析，保留原印象"
            impression, llm_tags = parsed
            # LLM 标签优先，确定性统计标签补充（去重、上限内）
            extra = [t for t in stats_tags(logs) if t not in llm_tags]
            tags = (llm_tags + extra)[:3]
            await self._storage.set_impression(group_id, user_id, impression, tags)
            logger.info(f"[心弦] {group_id}/{user_id} 印象已刷新: {impression}")
            return f"已生成印象：{impression}" + (f"（标签：{'、'.join(tags)}）" if tags else "")
        except Exception as e:
            logger.warning(f"[心弦] 印象刷新失败（静默）: {e}")
            return f"刷新失败: {e}"

    async def set_tags(self, group_id: str, user_id: str, tags: list[str]) -> None:
        """手动设置标签（指令入口）；印象本体由 AI 维护，这里只改标签。"""
        rec = await self.get(group_id, user_id)
        await self._storage.set_impression(
            group_id, user_id, rec.impression, [t.strip()[:6] for t in tags if t.strip()][:3]
        )

    # ---------- 增减 ----------

    async def apply_judge(
        self, group_id: str, user_id: str, delta: float, reason: str = "judge",
        *, message: str = "", umo: str = "",
    ) -> FavorChange:
        """应用 LLM 评估结果（judge 的冷却在 JudgeService 里处理）。

        先过防通胀经济学层（噪声地板 → 负面权重/阶段乘数 → 同日重复衰减），
        归零则不产生任何变动与流水。规则引擎/跨插件 API 不走该层。
        message: 触发本次评估的用户发言原文（截断 200 字入库，供 WebUI 核对是否误判）。
        umo: 会话标识（透传给印象刷新，用于解析当前人格名）。
        """
        if self._economy is not None:
            rec0 = await self.get(group_id, user_id)
            level_name = self.level_of(rec0.favor).name
            pos_today = await self._positive_judge_today(group_id, user_id)
            repair = await self._in_repair_window(group_id, user_id)
            eco = apply_economy(delta, level_name, pos_today, self._economy, repair=repair)
            if eco.delta == 0:
                return FavorChange(0, reason, "judge", clamped=True, favor_after=rec0.favor)
            delta = eco.delta
        change = await self._apply_one(
            group_id, user_id, delta,
            cooldown_key=None, cooldown_sec=0,
            reason=reason, source="judge",
            message=(message or "")[:200],
        )
        if change.delta:
            await self.maybe_refresh_impression(group_id, user_id, umo)
        return change

    async def _positive_judge_today(self, group_id: str, user_id: str) -> int:
        """当日已生效的正向评审次数（供同日重复衰减）。失败返回 0。"""
        try:
            start = self._day_start()
            logs = await self._storage.query_logs(group_id, user_id, limit=50)
            return sum(
                1 for r in logs
                if r.get("source") == "judge"
                and float(r.get("delta") or 0) > 0
                and float(r.get("ts") or 0) >= start
            )
        except Exception:
            return 0

    async def _in_repair_window(self, group_id: str, user_id: str) -> bool:
        """是否处于信任修复期：repair_hours 窗口内存在过 ≤ -repair_threshold 的
        重大得罪（评审流水，未撤销）。摧毁快、修复慢——窗口内正分被压制。
        失败（含未启用经济层）返回 False。"""
        eco = self._economy
        if eco is None or eco.repair_threshold <= 0 or eco.repair_hours <= 0:
            return False
        try:
            cutoff = time.time() - eco.repair_hours * 3600
            logs = await self._storage.query_logs(group_id, user_id, limit=50)
            return any(
                r.get("source") == "judge"
                and not r.get("reversed")
                and float(r.get("delta") or 0) <= -eco.repair_threshold
                and float(r.get("ts") or 0) >= cutoff
                for r in logs
            )
        except Exception:
            return False

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
        # 3. 落库（锁内原子，含 min_favor..max_favor 封顶 + 1 位小数收敛；
        #    无记录时以 default_favor 为基数，而非 0）
        rec, real = await self._storage.apply_delta(
            group_id, user_id, allowed, self.max_favor, self.min_favor,
            decay=self._decay, default_favor=self.default_favor,
        )
        if real:
            await self._storage.add_daily_gain(
                group_id, user_id, self._today().isoformat(), real
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
        today = self._today().isoformat()
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
