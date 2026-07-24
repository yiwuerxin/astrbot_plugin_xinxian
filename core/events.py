"""心弦好感度 - 规则事件。

定义事件类型、事件规则与消息匹配器。
规则驱动好感度变化的第一引擎（LLM 评估为第二引擎，见 services.judge_service）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class EventType(Enum):
    """好感度事件类型。"""

    PRAISED = auto()      # 被夸（关键词）
    INSULTED = auto()     # 被骂（关键词）
    AT_MENTION = auto()   # @小千
    REPLY_BOT = auto()    # 回复小千的消息
    DAILY_FIRST = auto()  # 当日首次互动


@dataclass(frozen=True)
class EventRule:
    """一条事件规则。

    Attributes:
        event: 事件类型。
        delta: 命中后的好感度变化（可负）。
        cooldown_sec: 同一成员该事件的冷却秒数，0 表示不走服务层冷却。
        keywords: 触发关键词（仅关键词类事件使用）。
        enabled: 是否启用。
    """

    event: EventType
    delta: int
    cooldown_sec: int = 0
    keywords: tuple[str, ...] = ()
    enabled: bool = True


def _split_keywords(raw: str, default: tuple[str, ...]) -> tuple[str, ...]:
    parts = [x.strip() for x in str(raw or "").replace("，", ",").split(",")]
    return tuple(x for x in parts if x) or default


def _rule_cfg(raw: dict, name: str, key: str, default):
    return (raw.get(name) or {}).get(key, default)


class RuleMatcher:
    """把一条群消息匹配成若干命中规则。

    同一条消息内同一 EventType 去重；只返回启用的规则。
    """

    def __init__(self, rules: list[EventRule]) -> None:
        self._rules = [r for r in rules if r.enabled]

    @classmethod
    def from_config(cls, cfg: dict | None) -> "RuleMatcher":
        """从插件配置构建匹配器；缺省项回落默认。"""
        raw = (cfg or {}).get("rules") or {}
        rules = [
            EventRule(
                EventType.PRAISED,
                delta=int(_rule_cfg(raw, "praised", "delta", 3)),
                cooldown_sec=int(_rule_cfg(raw, "praised", "cooldown_sec", 300)),
                keywords=_split_keywords(
                    _rule_cfg(raw, "praised", "keywords", ""),
                    ("厉害", "可爱", "喜欢", "棒", "谢谢", "爱你"),
                ),
                enabled=bool(_rule_cfg(raw, "praised", "enabled", True)),
            ),
            EventRule(
                EventType.INSULTED,
                delta=int(_rule_cfg(raw, "insulted", "delta", -5)),
                cooldown_sec=int(_rule_cfg(raw, "insulted", "cooldown_sec", 300)),
                keywords=_split_keywords(
                    _rule_cfg(raw, "insulted", "keywords", ""),
                    ("沙比", "笨", "蠢", "垃圾", "讨厌", "闭嘴"),
                ),
                enabled=bool(_rule_cfg(raw, "insulted", "enabled", True)),
            ),
            EventRule(
                EventType.AT_MENTION,
                delta=int(_rule_cfg(raw, "at_mention", "delta", 1)),
                cooldown_sec=int(_rule_cfg(raw, "at_mention", "cooldown_sec", 600)),
                enabled=bool(_rule_cfg(raw, "at_mention", "enabled", True)),
            ),
            EventRule(
                EventType.REPLY_BOT,
                delta=int(_rule_cfg(raw, "reply_bot", "delta", 1)),
                cooldown_sec=int(_rule_cfg(raw, "reply_bot", "cooldown_sec", 600)),
                enabled=bool(_rule_cfg(raw, "reply_bot", "enabled", True)),
            ),
            # DAILY_FIRST 不走服务层秒级冷却，由"是否当日首次"判定天然去重
            EventRule(
                EventType.DAILY_FIRST,
                delta=int(_rule_cfg(raw, "daily_first", "delta", 2)),
                cooldown_sec=0,
                enabled=bool(_rule_cfg(raw, "daily_first", "enabled", True)),
            ),
        ]
        return cls(rules)

    def match(
        self,
        text: str,
        *,
        has_at_bot: bool = False,
        is_reply_bot: bool = False,
        is_first_today: bool = False,
    ) -> list[EventRule]:
        """返回该消息命中的全部启用规则（同类去重）。"""
        hits: list[EventRule] = []
        seen: set[EventType] = set()
        for rule in self._rules:
            if rule.event in seen:
                continue
            hit = False
            if rule.event in (EventType.PRAISED, EventType.INSULTED):
                hit = any(k in text for k in rule.keywords)
            elif rule.event == EventType.AT_MENTION:
                hit = has_at_bot
            elif rule.event == EventType.REPLY_BOT:
                hit = is_reply_bot
            elif rule.event == EventType.DAILY_FIRST:
                hit = is_first_today
            if hit:
                hits.append(rule)
                seen.add(rule.event)
        return hits
