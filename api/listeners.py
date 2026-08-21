"""心弦好感度 - 事件监听 handler。

on_group_message：触发 LLM 评估（好感度变化的唯一自动引擎）。
on_llm_request：向 system_prompt 注入好感度档案。
身份一律从事件取（get_sender_id/get_group_id），不解析任何注入文本。
"""

from __future__ import annotations

from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest

from ..core.decimal import fmt
from ..services.favor_service import FavorService
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


async def on_group_message(deps: Deps, event: AstrMessageEvent) -> None:
    """群消息入口：评估引擎（唯一自动引擎，内部自行判断开关/冷却/降级）。"""
    group_id, user_id = event.get_group_id(), event.get_sender_id()
    if not group_id or not user_id:
        return
    if user_id == str(event.get_self_id() or ""):
        return  # 自己的消息不计

    # 顺手记下昵称（供排行图/WebUI 显示名字）；带内存缓存，未变不写库
    try:
        _nick = event.get_sender_name()
    except Exception:
        _nick = None
    if _nick:
        await deps.favor.touch_nickname(group_id, user_id, _nick)

    text = event.message_str or ""
    has_at_bot, is_reply_bot = _chain_flags(event)

    # 评估引擎（内部自行判断开关/冷却/降级）
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


async def on_llm_request(
    deps: Deps, event: AstrMessageEvent, req: ProviderRequest
) -> None:
    """LLM 请求前：注入好感度档案（仅群聊，私聊跳过）。"""
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
    events = await deps.favor.recent_events(
        group_id, user_id, deps.memory_count, deps.memory_days,
        sig_threshold=deps.memory_sig_threshold,
        sig_window_mult=deps.memory_sig_window_mult,
    )
    block = deps.inject.build_block(
        rec,
        is_master=deps.favor.is_master(user_id),
        nickname=nickname,
        recent_events=events,
    )
    deps.inject.inject(req, block)
