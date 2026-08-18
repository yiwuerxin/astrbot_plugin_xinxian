"""心弦好感度 - LLM 情绪评估服务。

让小模型判断"这句话对小千是善意还是恶意"，微调好感度。
设计原则：任何环节失败都静默降级（返回 None），绝不影响正常对话。
成本控制：仅@/回复时评估（可配）+ 每人冷却 + 幅度限幅 + 总开关。
"""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..core.judge_context import extract_history_text
from ..core.judge_parse import ParsedJudge, parse as parse_judge
from ..core.judge_prompt import render
from ..storage.base import StorageBackend


@dataclass
class JudgeResult:
    """一次评估结果。"""

    delta: float     # 调整分值（档位映射+限幅，精度一位小数），0 = 中性
    attitude: str    # 五档：敌意 / 冷淡 / 中性 / 友好 / 热情
    raw: str         # 模型原始输出
    reason: str = ""  # 变动理由（narrative 模式下有值）
    evidence: str = ""  # 非中性档位的原话证据（解析层强制要求）


class JudgeService:
    """LLM 情绪评估服务。"""

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
        follow_persona: bool = True,
        bot_name: str = "小千",
        attitude_deltas: dict[str, float] | None = None,
        roster: str = "",
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
        self._follow_persona = follow_persona
        self._bot_name = bot_name
        self._attitude_deltas = attitude_deltas or {}
        self._roster = (roster or "").strip()

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
            persona_name, persona_prompt = await self._persona_ctx(event)
            prompt = render(
                self._prompt_template,
                text=text.strip(),
                persona_name=persona_name,
                persona_prompt=persona_prompt,
                roster=self._roster,
            )
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
            umo = getattr(event, "unified_msg_origin", "") if event else ""
            res = self._context.get_using_provider(umo)
            return await res if inspect.isawaitable(res) else res
        except Exception as e:
            logger.warning(f"[心弦] 获取 provider 失败（已静默降级）: {e}")
            return None

    async def _persona_ctx(self, event: AstrMessageEvent) -> tuple[str, str]:
        """解析当前会话生效的人格（名称 + 人设 prompt），评审提示词随人格切换同步。

        与 AstrBot 主链路同源：conv.persona_id → persona_manager.resolve_selected_persona。
        任何失败（旧版框架无该 API / 无会话 / 解析异常）静默回落 (bot_name, "")。
        """
        if not self._follow_persona:
            return self._bot_name, ""
        try:
            pm = getattr(self._context, "persona_manager", None)
            if pm is None:
                return self._bot_name, ""
            umo = getattr(event, "unified_msg_origin", "") or ""
            conv_persona = None
            cm = getattr(self._context, "conversation_manager", None)
            if umo and cm:
                cid = await cm.get_curr_conversation_id(umo)
                conv = await cm.get_conversation(umo, cid) if cid else None
                conv_persona = getattr(conv, "persona_id", None) if conv else None
            provider_settings: dict = {}
            try:
                cfg = self._context.get_config(umo or None)
                provider_settings = (cfg or {}).get("provider_settings", {}) or {}
            except Exception:
                pass
            persona_id, persona, _, _ = await pm.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=conv_persona,
                platform_name=event.get_platform_name(),
                provider_settings=provider_settings,
            )
            if persona:
                name = str(persona.get("name") or persona_id or self._bot_name)
                return name, str(persona.get("prompt") or "")
        except Exception as e:
            logger.debug(f"[心弦] 人格解析失败，评审回落默认称呼: {e}")
        return self._bot_name, ""

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
                # 提取纯文本（4.26 的 content 可能是结构化列表）；
                # 图片/工具调用/think 等不带文本的部分在提取时跳过
                content = extract_history_text(rec.get("content"))
                if role in ("user", "assistant") and content:
                    contexts.append({"role": role, "content": content})
            return contexts
        except Exception as e:
            logger.warning(f"[心弦] 取最近上下文失败（已降级为零上下文）: {e}")
            return []

    def _parse(self, content: str) -> JudgeResult | None:
        """解析模型输出 → 五档 ParsedJudge（旧格式自动兼容），映射为 JudgeResult。"""
        parsed: ParsedJudge | None = parse_judge(
            content, self._attitude_deltas, max_abs_delta=self._max_abs_delta
        )
        if parsed is None:
            return None
        return JudgeResult(
            delta=parsed.delta,
            attitude=parsed.tier,
            raw=content,
            reason=parsed.reason,
            evidence=parsed.evidence,
        )
