"""心弦好感度 - LLM 情绪评估服务。

让小模型判断"这句话对小千是善意还是恶意"，微调好感度。
设计原则：任何环节失败都静默降级（返回 None），绝不影响正常对话。
成本控制：仅@/回复时评估（可配）+ 每人冷却 + 幅度限幅 + 总开关。
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..storage.base import StorageBackend


@dataclass
class JudgeResult:
    """一次评估结果。"""

    delta: int       # 调整分值（已限幅），0 = 中性
    attitude: str    # 友好 / 敌意 / 中性
    raw: str         # 模型原始输出


class JudgeService:
    """LLM 情绪评估服务。"""

    _PARSE_RE = re.compile(
        r"态度[:：]\s*(友好|善意|敌意|恶意|中性)[\s\S]*?分值[:：]\s*([+-]?\d+)"
    )

    def __init__(
        self,
        context: Context,
        storage: StorageBackend,
        *,
        enabled: bool,
        provider_id: str | None,
        cooldown_sec: int,
        max_abs_delta: int,
        only_when_at_or_reply: bool,
        prompt_template: str,
    ) -> None:
        self._context = context
        self._storage = storage
        self._enabled = enabled
        self._provider_id = provider_id
        self._cooldown_sec = cooldown_sec
        self._max_abs_delta = max_abs_delta
        self._only_when_at_or_reply = only_when_at_or_reply
        self._prompt_template = prompt_template

    async def judge(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        has_at_bot: bool = False,
        is_reply_bot: bool = False,
    ) -> JudgeResult | None:
        """评估一条消息对小千的态度。返回 None 表示跳过或降级。"""
        if not self._enabled or not text.strip():
            return None
        if self._only_when_at_or_reply and not (has_at_bot or is_reply_bot):
            return None

        group_id, user_id = event.get_group_id(), event.get_sender_id()
        now = time.time()
        last = await self._storage.last_event_at(group_id, user_id, "judge")
        if last is not None and now - last < self._cooldown_sec:
            return None

        provider = await self._resolve_provider(event)
        if provider is None:
            return None

        try:
            prompt = self._prompt_template.format(text=text.strip())
            try:
                resp = await provider.text_chat(prompt=prompt, contexts=[])
            except TypeError:
                resp = await provider.text_chat(prompt)
            content = (getattr(resp, "completion_text", "") or "").strip()
        except Exception as e:
            logger.warning(f"[心弦] judge 调用失败（已静默降级）: {e}")
            return None

        result = self._parse(content)
        if result is None:
            return None
        await self._storage.touch_event(group_id, user_id, "judge", now)
        return result

    async def _resolve_provider(self, event: AstrMessageEvent):
        """解析评估用模型：配置非空按 id 取，否则跟随会话当前模型。"""
        try:
            if self._provider_id:
                return self._context.get_provider_by_id(self._provider_id)
            umo = getattr(event, "unified_msg_origin", "")
            res = self._context.get_using_provider(umo)
            return await res if inspect.isawaitable(res) else res
        except Exception as e:
            logger.warning(f"[心弦] 获取 provider 失败（已静默降级）: {e}")
            return None

    def _parse(self, content: str) -> JudgeResult | None:
        """解析模型输出（态度:xx 分值:±n），限幅并校验符号一致性。"""
        m = self._PARSE_RE.search(content)
        if not m:
            return None
        attitude, raw_delta = m.group(1), int(m.group(2))
        if attitude in ("友好", "善意"):
            delta = abs(raw_delta)
        elif attitude in ("敌意", "恶意"):
            delta = -abs(raw_delta)
        else:
            delta = 0
        delta = max(-self._max_abs_delta, min(self._max_abs_delta, delta))
        return JudgeResult(delta=delta, attitude=attitude, raw=content)
