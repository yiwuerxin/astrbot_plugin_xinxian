"""心弦好感度 - 成员印象服务。

从 FavorService 拆出（v1.29.3）：印象是挂在评审链路上的增值功能，
不该让"好感度数值门面"反向依赖评审服务（原先经 bind_summarizer
形成 services 内部的隐式环）。本服务只持 storage 与 summarizer
（JudgeService 的公共能力接口），独立完成"收集流水 → 廉价 LLM 汇总 →
解析落库"与手动标签。所有失败静默——印象是增强，永远不是依赖。
"""

from __future__ import annotations

import asyncio

from ..core.impression import build_summary_prompt, parse_summary, stats_tags
from ..storage.base import StorageBackend


class ImpressionService:
    """成员印象与标签的维护服务。"""

    def __init__(
        self,
        storage: StorageBackend,
        *,
        interval: int = 8,
        summarizer=None,
        timeout_sec: float = 60.0,
        registry=None,
        points_mode: bool = False,
    ) -> None:
        self._storage = storage
        self._interval = max(1, int(interval))
        self._summarizer = (
            summarizer  # main.py 注入（JudgeService，借其 provider 解析）
        )
        self._points_mode = points_mode  # P-C：带权印象点模式（默认关=legacy 一句话）
        # X3：汇总调用超时（与评审同源配置）——挂起的 provider 会把后台
        # 任务永久滞留在注册表里
        self._timeout_sec = max(0.0, float(timeout_sec))
        # X10：后台任务经 TaskRegistry 发起（无注册表时本地集合兜底）
        self._registry = registry

    async def _call_llm(self, provider, prompt: str):
        """带超时的 text_chat（X3）；超时抛 TimeoutError 由调用方降级。"""
        call = provider.text_chat(prompt=prompt)
        if self._timeout_sec > 0:
            call = asyncio.wait_for(call, timeout=self._timeout_sec)
        return await call

    def bind_summarizer(self, judge) -> None:
        """注入 JudgeService（借其 provider 解析与人格名，用于印象汇总调用）。"""
        self._summarizer = judge

    async def maybe_refresh(self, group_id: str, user_id: str, umo: str = "") -> None:
        """每次有效评审后调用：每 interval 次触发一次后台印象刷新（不阻塞、失败静默）。

        umo: 会话标识（用于解析当前人格名）；后台任务持引用防 GC。
        触发条件＝最近 interval 条流水全部是评审行（拿流水当计数器，免加
        计数列）；中间插进的管理员操作会顺延一次刷新，可接受。
        """
        if self._summarizer is None or self._interval <= 0:
            return
        try:
            logs = await self._storage.query_logs(
                group_id, user_id, limit=self._interval
            )
            hit = sum(1 for r in logs if r.get("source") == "judge")
            if hit and hit % self._interval == 0:
                coro = self._refresh_inner(group_id, user_id, umo)
                if self._registry is not None:
                    self._registry.spawn(coro, name=f"impression:{group_id}/{user_id}")
                else:
                    task = asyncio.create_task(coro)  # 测试兜底：本例无后续引用需求
        except Exception as e:
            # 印象是增值功能，任何失败都不影响主链路——但触发调度失败
            # 连 spawn 都没发生，运维必须可见（2026-09-11 审查：原为纯 pass）
            from astrbot.api import logger as _logger

            _logger.debug(f"[心弦] 印象刷新调度失败（跳过本轮）: {e}", exc_info=True)

    async def refresh_now(
        self, group_id: str, user_id: str, umo: str = ""
    ) -> tuple[bool, str]:
        """立即刷新印象（指令/WebUI 手动触发），返回 (是否成功, 一句话结果)。"""
        if self._summarizer is None:
            return False, "印象功能未启用"
        return await self._refresh_inner(group_id, user_id, umo)

    async def _refresh_inner(
        self, group_id: str, user_id: str, umo: str = ""
    ) -> tuple[bool, str]:
        """收集流水 → 构造汇总提示 → 调 provider → 解析落库。"""
        from astrbot.api import logger  # 延迟导入，保持本模块可脱离框架测试

        try:
            rec = await self._storage.get(group_id, user_id)
            nickname = (rec.nickname if rec else "") or user_id
            old_impression = (rec.impression if rec else "") or ""
            logs = await self._storage.query_logs(group_id, user_id, limit=20)
            judged = [r for r in logs if r.get("source") == "judge"]
            if not judged:
                return False, "暂无评估记录，无法生成印象"
            samples = [
                f"{float(r.get('delta') or 0):+} {r.get('message') or ''} —— {r.get('reason') or ''}"
                for r in reversed(judged)  # 时间正序（query_logs 是倒序）
            ]
            # 人格名与 provider 走 JudgeService 公共方法（解析当前会话人格，
            # 失败回落 bot_name），与评审链路同源
            persona_name = await self._summarizer.resolve_display_name(umo)
            provider = await self._summarizer.resolve_summary_provider()
            if provider is None:
                return False, "模型不可用，稍后再试"
            if self._points_mode:
                return await self._refresh_points(
                    group_id,
                    user_id,
                    nickname,
                    old_impression,
                    samples,
                    persona_name,
                    judged,
                    provider,
                    logger,
                )
            prompt = build_summary_prompt(
                nickname, old_impression, samples, persona_name
            )
            resp = await self._call_llm(provider, prompt)
            content = (getattr(resp, "completion_text", "") or "").strip()
            parsed = parse_summary(content)
            if parsed is None:
                return False, "模型输出无法解析，保留原印象"
            impression, llm_tags = parsed
            # LLM 标签优先，确定性统计标签补充（去重、上限内）
            extra = [t for t in stats_tags(logs) if t not in llm_tags]
            tags = (llm_tags + extra)[:3]
            await self._storage.set_impression(group_id, user_id, impression, tags)
            logger.info(f"[心弦] {group_id}/{user_id} 印象已刷新: {impression}")
            msg = f"已生成印象：{impression}" + (
                f"（标签：{'、'.join(tags)}）" if tags else ""
            )
            return True, msg

        except Exception as e:
            logger.warning(f"[心弦] 印象刷新失败（静默）: {e}")
            return False, f"刷新失败: {e}"

    async def _refresh_points(
        self,
        group_id,
        user_id,
        nickname,
        old_impression,
        samples,
        persona_name,
        judged,
        provider,
        logger,
    ) -> tuple[bool, str]:
        """P-C 带权点模式：LLM 提点 → 损失厌恶加权 → 相似合并 → 限量保留。"""
        import time as _time
        from ..core.impression_points import (
            POINTS_PROMPT,
            anonymize,
            loss_aversion_multiplier,
            merge_points,
            parse_points,
            render_impression,
            retain,
        )

        try:
            now = _time.time()
            rec = await self._storage.get(group_id, user_id)
            existing = rec.parsed_points() if rec else []
            # 分析提示词里其他群友昵称匿名化（防串像）；最近负向变化 ×1.5 损失厌恶
            others = []
            try:
                rows = await self._storage.list_favor(group_id, limit=200)
                others = [
                    r.nickname for r in rows if r.nickname and r.user_id != user_id
                ]
            except Exception:
                pass
            anon_samples = "\n".join(anonymize(x, others) for x in samples)
            old_line = (
                (
                    f"既有印象点（权重 1~10）：\n"
                    + "\n".join(
                        f"- {p.get('point')}（{p.get('weight')}）"
                        for p in existing[:10]
                    )
                    + "\n"
                )
                if existing
                else ""
            )
            recent_delta = float(judged[0].get("delta") or 0) if judged else 0.0
            prompt = POINTS_PROMPT.format(
                persona_name=persona_name,
                who=nickname or user_id,
                old_points=old_line,
                samples=anon_samples or "（暂无记录）",
            )
            resp = await self._call_llm(provider, prompt)
            content = (getattr(resp, "completion_text", "") or "").strip()
            new_pts = parse_points(content)
            if not new_pts:
                return False, "模型输出无法解析，保留原印象点"
            mult = loss_aversion_multiplier(recent_delta)
            for p in new_pts:
                p["weight"] = max(1, min(15, int(round(p["weight"] * mult))))
                p["ts"] = now
            merged = merge_points(existing, new_pts)
            kept, dropped = retain(merged, now)
            impression = render_impression(kept)
            if dropped:
                # 挤出项并入长印象（Sourcery：不得以旧印象为空为由丢弃挤出点）
                prefix = (old_impression + "；") if old_impression else ""
                tail = "；较早印象：" + "；".join(d["point"] for d in dropped)
                impression = (prefix + impression + tail)[:200]
            # 原子档案更新：点集/印象/标签一次写入（失败即整体不生效，可安全重试）
            old_tags = rec.parsed_tags() if rec else []
            await self._storage.set_profile(
                group_id, user_id, impression, old_tags, kept
            )
            logger.info(
                f"[心弦] {group_id}/{user_id} 印象点已刷新: "
                f"{len(kept)} 点（挤出 {len(dropped)}）"
            )
            return True, f"已生成印象：{impression}"
        except Exception as e:
            logger.warning(f"[心弦] 印象点刷新失败（静默）: {e}")
            return False, f"刷新失败: {e}"

    async def set_tags(self, group_id: str, user_id: str, tags: list[str]) -> None:
        """手动设置标签（指令入口）；印象本体由 AI 维护，这里只改标签。"""
        rec = await self._storage.get(group_id, user_id)
        await self._storage.set_impression(
            group_id,
            user_id,
            (rec.impression if rec else ""),
            [t.strip()[:6] for t in tags if t.strip()][:3],
        )
