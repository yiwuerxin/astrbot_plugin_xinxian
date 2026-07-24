"""心弦好感度 - 关系类型。

给每个成员打一个关系标签（朋友/挚友/恋人/家人/讨厌的人…），
与好感度数值正交：数值管冷热，关系类型管角色。注入对应语气框架。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationshipType:
    """一种关系类型。

    Attributes:
        key: 存储与指令用的键（如 lover）。
        label: 展示名（如 恋人）。
        guidance: 注入用的语气指引。
    """

    key: str
    label: str
    guidance: str


DEFAULT_TYPES: list[RelationshipType] = [
    RelationshipType("friend", "朋友", "把 TA 当朋友：平等、随和，可以开玩笑"),
    RelationshipType("soulmate", "挚友", "把 TA 当无话不谈的知己：彼此袒护、情绪上依赖 TA"),
    RelationshipType("lover", "恋人", "把 TA 当恋人：会吃醋、撒娇、依赖，把 TA 放在心上"),
    RelationshipType("family", "家人", "把 TA 当家人：自然亲近、不用客套"),
    RelationshipType("disliked", "讨厌的人", "反感 TA，保持距离、态度冷淡"),
]


class RelationshipTable:
    """关系类型表：按 key 解析 (label, guidance)；也兼容自定义 label。"""

    def __init__(self, types: list[RelationshipType]) -> None:
        self._types = {t.key: t for t in types}

    @classmethod
    def from_config(cls, cfg: dict | None) -> "RelationshipTable":
        """从插件配置构建；缺省项回落默认。"""
        raw = (cfg or {}).get("types") or {}
        types: list[RelationshipType] = []
        for default in DEFAULT_TYPES:
            item = raw.get(default.key) or {}
            types.append(
                RelationshipType(
                    key=default.key,
                    label=str(item.get("label") or default.label),
                    guidance=str(item.get("guidance") or default.guidance),
                )
            )
        return cls(types)

    def keys(self) -> list[str]:
        """全部预设键（供指令提示）。"""
        return list(self._types.keys())

    def label_of(self, key: str) -> str:
        """键 → 展示名；未知键原样返回。"""
        t = self._types.get((key or "").strip())
        return t.label if t else (key or "")

    def resolve(self, value: str) -> tuple[str, str] | None:
        """存储值 → (展示label, guidance)；空或未设置返回 None。

        未知值视为自定义 label，给通用 guidance。
        """
        value = (value or "").strip()
        if not value:
            return None
        t = self._types.get(value)
        if t:
            return t.label, t.guidance
        return value, f"请按「{value}」这个关系角色调整你对 TA 的态度"
