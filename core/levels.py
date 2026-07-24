"""心弦好感度 - 等级划分。

陌生 → 认识 → 友好 → 亲密 → 挚友 → 挚爱，阈值与态度指引均可配置。
"""

from __future__ import annotations

from .models import LevelDef

DEFAULT_LEVELS: list[LevelDef] = [
    LevelDef("陌生", 0, 9, "礼貌而疏离，保持分寸，不主动亲昵"),
    LevelDef("认识", 10, 29, "友善客气，像刚认识的朋友"),
    LevelDef("友好", 30, 54, "放松自然，会开玩笑、主动接话"),
    LevelDef("亲密", 55, 79, "亲昵随意，会撒娇、记挂对方，偶尔吃醋"),
    LevelDef("挚友", 80, 94, "无话不谈，袒护 TA，情绪上依赖 TA"),
    LevelDef("挚爱", 95, -1, "全身心信任与依恋，把 TA 放在所有人之前"),
]

# 等级名 -> 配置键（_conf_schema.json 中 levels 下的键）
_LEVEL_KEYS = {
    "陌生": "mosheng",
    "认识": "renshi",
    "友好": "youhao",
    "亲密": "qinmi",
    "挚友": "zhiyou",
    "挚爱": "zhiai",
}


class LevelTable:
    """等级表：根据好感度数值查等级。按 min_score 升序匹配最后一个满足项。"""

    def __init__(self, levels: list[LevelDef]) -> None:
        if not levels:
            raise ValueError("levels 不能为空")
        self._levels = sorted(levels, key=lambda lv: lv.min_score)

    @classmethod
    def from_config(cls, cfg: dict | None) -> "LevelTable":
        """从插件配置构建等级表；缺失项回落到默认值。

        Args:
            cfg: 插件配置（含 levels 对象的 dict），可为 None 或缺省。
        """
        raw = (cfg or {}).get("levels") or {}
        levels: list[LevelDef] = []
        for default in DEFAULT_LEVELS:
            item = raw.get(_LEVEL_KEYS[default.name]) or {}
            levels.append(
                LevelDef(
                    name=default.name,
                    min_score=int(item.get("min", default.min_score)),
                    max_score=int(item.get("max", default.max_score)),
                    guidance=str(item.get("guidance") or default.guidance),
                )
            )
        return cls(levels)

    def level_of(self, favor: int) -> LevelDef:
        """返回数值对应的等级定义。"""
        result = self._levels[0]
        for lv in self._levels:
            if favor >= lv.min_score:
                result = lv
            else:
                break
        return result

    def all(self) -> list[LevelDef]:
        """返回全部等级（升序）。"""
        return list(self._levels)
