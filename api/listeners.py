"""心弦好感度 - 事件监听 handler。

on_group_message：触发 LLM 评估（好感度变化的唯一自动引擎）。
on_llm_request：向 system_prompt 注入好感度档案。
身份一律从事件取（get_sender_id/get_group_id），不解析任何注入文本。
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest

from ..core.decimal import fmt
from ..core.taskregistry import TaskRegistry
from ..services.favor_service import FavorService
from ..services.impression_service import ImpressionService
from ..services.inject_service import InjectService
from ..services.judge_service import JudgeService

try:  # 消息组件（兼容新旧导入路径）
    from astrbot.api.message_components import At, Reply
except ImportError:  # pragma: no cover
    from astrbot.core.message.components import At, Reply


@dataclass
class Deps:
    """监听器依赖包（由 main.py 装配）。"""

    favor: FavorService
    judge: JudgeService
    inject: InjectService
    impressions: ImpressionService | None = None
    registry: TaskRegistry | None = None  # X10：后台任务注册表（main 注入）
    inject_enabled: bool = True
    memory_count: int = 3
    memory_days: int = 7
    memory_sig_threshold: float = 0.0
    memory_sig_window_mult: float = 1.0


def _chain_flags(event: AstrMessageEvent) -> tuple[bool, bool]:
    """解析消息链：是否@小千、是否回复小千。"""
    self_id = str(event.get_self_id() or "")
    chain = getattr(event.message_obj, "message", None) or []
    has_at_bot = any(
        isinstance(c, At) and str(getattr(c, "qq", "")) == self_id for c in chain
    )
    is_reply_bot = any(
        isinstance(c, Reply) and str(getattr(c, "sender_id", "")) == self_id
        for c in chain
    )
    return has_at_bot, is_reply_bot


# X10：后台评审任务经 Deps.registry（TaskRegistry）发起——裸 create_task
# 只被事件循环弱引用可能被 GC 中途回收，且卸载时需 cancel_and_wait


async def on_group_message(deps: Deps, event: AstrMessageEvent) -> None:
    """群消息入口：评估引擎（唯一自动引擎，内部自行判断开关/冷却/降级）。

    评审是事后打分（评的是已说出口的话），结果只影响下一条消息的注入——
    因此整个评审+落库放后台任务，不阻塞本条消息的回复链路（AstrBot 的
    pipeline 按 stage 串行 await，同步评审会让 @ 消息先卡一次评审 LLM 调用）。
    评审本身 fail-silent：后台任务的异常只记日志。
    """
    group_id, user_id = event.get_group_id(), event.get_sender_id()
    if not group_id or not user_id:
        return
    if user_id == str(event.get_self_id() or ""):
        return  # 自己的消息不计

    # 消息链与文本在调度前取好（事件对象在后台任务里不再可靠）
    try:
        _nick = event.get_sender_name()
    except Exception:
        _nick = None
    text = event.message_str or ""
    has_at_bot, is_reply_bot = _chain_flags(event)

    async def _bg() -> None:
        try:
            if _nick:
                await deps.favor.touch_nickname(group_id, user_id, _nick)
            result = await deps.judge.judge(
                event, text, has_at_bot=has_at_bot, is_reply_bot=is_reply_bot
            )
            if result is not None and result.delta:
                change = await deps.favor.apply_judge(
                    group_id, user_id, result.delta,
                    reason=result.reason or f"judge:{result.attitude}",
                    message=text,
                )
                if change.delta:
                    logger.info(
                        f"[心弦] {group_id}/{user_id} 评估[{result.attitude}] "
                        f"{change.delta:+.1f} -> {fmt(change.favor_after)}"
                    )
                    if deps.impressions is not None:
                        await deps.impressions.maybe_refresh(
                            group_id, user_id,
                            umo=getattr(event, "unified_msg_origin", "") or "",
                        )
        except Exception:
            logger.warning("[心弦] 后台评估任务异常（忽略，不影响对话）")

    if deps.registry is not None:
        deps.registry.spawn(_bg(), name=f"judge:{group_id}/{user_id}")
    else:
        asyncio.create_task(_bg())  # 无注册表兜底（仅测试）


async def on_llm_request(
    deps: Deps, event: AstrMessageEvent, req: ProviderRequest
) -> None:
    """LLM 请求前：注入好感度档案（仅群聊，私聊跳过）。

    X2：注入是每次对话的必经路径而非增值功能——任何失败（存储/渲染/
    模板）只 warning 并跳过本次注入，绝不把异常抛进框架钩子打断回复。"""
    try:
        await _on_llm_request_inner(deps, event, req)
    except Exception:
        logger.warning("[心弦] 注入链路异常，本次跳过注入（不影响对话）", exc_info=True)


async def _on_llm_request_inner(
    deps: Deps, event: AstrMessageEvent, req: ProviderRequest
) -> None:
    if not deps.inject_enabled:
        return
    group_id, user_id = event.get_group_id(), event.get_sender_id()
    if not group_id or not user_id:
        return
    rec = await deps.favor.get(group_id, user_id)
    try:
        nickname = event.get_sender_name()
    except Exception:
        nickname = None
    logs = await deps.favor.query_logs(group_id, user_id, limit=15)
    # recent_events 是 async def——漏 await 会返回协程，注入链路在
    # _format_events 的 for 循环上炸 TypeError: 'coroutine' object is not iterable
    events = await deps.favor.recent_events(
        group_id, user_id, deps.memory_count, deps.memory_days,
        logs=logs,
        sig_threshold=deps.memory_sig_threshold,
        sig_window_mult=deps.memory_sig_window_mult,
    )
    milestone = deps.favor.recent_milestone(group_id, user_id, logs=logs)
    block = deps.inject.build_block(
        rec,
        is_master=deps.favor.is_master(user_id),
        nickname=nickname,
        recent_events=events,
        milestone=milestone,
    )
    deps.inject.inject(req, block)
