"""心弦好感度 - LLM 情绪评估服务。

让小模型判断"这句话对小千是善意还是恶意"，微调好感度。
设计原则：任何环节失败都静默降级（返回 None），绝不影响正常对话。
成本控制：仅@/回复时评估（可配）+ 每人冷却 + 幅度限幅 + 总开关。
"""

from __future__ import annotations

import inspect
import json
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

    delta: float     # 调整分值（已限幅，精度一位小数），0 = 中性
    attitude: str    # 友好 / 敌意 / 中性
    raw: str         # 模型原始输出
    reason: str = ""  # 主模型口吻的变动理由（narrative 模式下有值）


class JudgeService:
    """LLM 情绪评估服务。"""

    _PARSE_RE = re.compile(
        r"态度[:：]\s*(友好|善意|敌意|恶意|中性)[\s\S]*?"
        r"分值[:：]\s*([+-]?\d+)"
        r"(?:[\s\S]*?理由[:：]\s*(.+))?"  # narrative 模式才有；可选
    )

    def __init__(
        self,
        context: Context,
        storage: StorageBackend,
        *,
        enabled: bool,
        provider_id: str | None,
        cooldown_sec: int,
        max_abs_delta: float,
        only_when_at_or_reply: bool,
        prompt_template: str,
        force_session_model: bool = False,
        context_window: int = 0,
    ) -> None:
        self._context = context
        self._storage = storage
        self._enabled = enabled
        self._provider_id = provider_id
        self._cooldown_sec = cooldown_sec
        self._max_abs_delta = max_abs_delta
        self._only_when_at_or_reply = only_when_at_or_reply
        self._prompt_template = prompt_template
        self._force_session_model = force_session_model
        self._context_window = context_window

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
            contexts = await self._recent_context(event)
            try:
                resp = await provider.text_chat(prompt=prompt, contexts=contexts)
            except TypeError:
                resp = await provider.text_chat(prompt)  # 不支持 contexts 的 provider：退化为无上下文
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
        """解析评估用模型：force_session_model 或 provider_id 留空时，跟随会话当前（主）模型。"""
        try:
            if self._provider_id and not self._force_session_model:
                return self._context.get_provider_by_id(self._provider_id)
            umo = getattr(event, "unified_msg_origin", "")
            res = self._context.get_using_provider(umo)
            return await res if inspect.isawaitable(res) else res
        except Exception as e:
            logger.warning(f"[心弦] 获取 provider 失败（已静默降级）: {e}")
            return None

    async def _recent_context(self, event: AstrMessageEvent) -> list[dict]:
        """取最近 context_window 条会话消息作为评估上下文（仅文本，忽略图片）。失败返回 []。"""
        n = self._context_window
        if not n or n <= 0:
            return []
        try:
            umo = getattr(event, "unified_msg_origin", "") or ""
            cm = getattr(self._context, "conversation_manager", None)
            get_cid = getattr(cm, "get_curr_conversation_id", None) if cm else None
            if not umo or not callable(get_cid):
                return []
            cid = await get_cid(umo)
            if not cid:
                return []
            conv = await cm.get_conversation(umo, cid)
            if not conv:
                return []
            history = json.loads(getattr(conv, "history", "") or "[]")
            contexts: list[dict] = []
            for rec in history[-int(n):]:
                role = rec.get("role")
                content = rec.get("content")
                # 只保留有文本内容的消息；图片/工具调用等不带文本的直接跳过
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    contexts.append({"role": role, "content": content})
            return contexts
        except Exception as e:
            logger.warning(f"[心弦] 取最近上下文失败（已降级为零上下文）: {e}")
            return []

    def _parse(self, content: str) -> JudgeResult | None:
        """解析模型输出（态度:xx 分值:±n[ 理由:...]），限幅并校验符号一致性。"""
        m = self._PARSE_RE.search(content)
        if not m:
            return None
        attitude, raw_delta = m.group(1), float(m.group(2))
        reason = (m.group(3) or "").strip()  # narrative 模式才有
        if attitude in ("友好", "善意"):
            delta = abs(raw_delta)
        elif attitude in ("敌意", "恶意"):
            delta = -abs(raw_delta)
        else:
            delta = 0
        delta = max(-self._max_abs_delta, min(self._max_abs_delta, delta))
        return JudgeResult(delta=delta, attitude=attitude, raw=content, reason=reason)
