"""心弦好感度 - 规则事件。

规则引擎：仅「当日首次互动」一种事件（每日首次与小千互动，给予少量好感度）。
LLM 情绪评估为第二引擎，见 services.judge_service。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class EventType(Enum):
    """好感度事件类型。"""

    DAILY_FIRST = auto()  # 当日首次互动


@dataclass(frozen=True)
class EventRule:
    """一条事件规则。

    Attributes:
        event: 事件类型。
        delta: 命中后的好感度变化（可负）。
        cooldown_sec: 同一成员该事件的冷却秒数，0 表示不走服务层冷却。
        enabled: 是否启用。
    """

    event: EventType
    delta: float
    cooldown_sec: int = 0
    enabled: bool = True


def _rule_cfg(raw: dict, name: str, key: str, default):
    return (raw.get(name) or {}).get(key, default)


class RuleMatcher:
    """把一条群消息匹配成命中的规则。只返回启用的规则。"""

    def __init__(self, rules: list[EventRule]) -> None:
        self._rules = [r for r in rules if r.enabled]

    @classmethod
    def from_config(cls, cfg: dict | None) -> "RuleMatcher":
        """从插件配置构建匹配器；缺省项回落默认。"""
        raw = (cfg or {}).get("rules") or {}
        # DAILY_FIRST 不走服务层秒级冷却，由"是否当日首次"判定天然去重
        rules = [
            EventRule(
                EventType.DAILY_FIRST,
                delta=float(_rule_cfg(raw, "daily_first", "delta", 2)),
                cooldown_sec=0,
                enabled=bool(_rule_cfg(raw, "daily_first", "enabled", True)),
            ),
        ]
        return cls(rules)

    def match(self, *, is_first_today: bool = False) -> list[EventRule]:
        """返回该消息命中的全部启用规则。"""
        hits: list[EventRule] = []
        for rule in self._rules:
            if rule.event == EventType.DAILY_FIRST and is_first_today:
                hits.append(rule)
        return hits
