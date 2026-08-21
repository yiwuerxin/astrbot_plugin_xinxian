"""心弦好感度 - 等级划分。

陌生 → 认识 → 友好 → 亲密 → 挚友 → 挚爱，阈值与态度指引均可配置。
"""

from __future__ import annotations

from .models import LevelDef

DEFAULT_LEVELS: list[LevelDef] = [
    LevelDef("厌恶", -100, -1, "反感与回避，冷淡敷衍，抗拒亲近，语气生硬拒人于千里之外"),
    LevelDef("陌生", 0, 9, "礼貌而疏离，保持分寸，不主动亲昵"),
    LevelDef("认识", 10, 29, "友善客气，像刚认识的朋友"),
    LevelDef("友好", 30, 54, "放松自然，会开玩笑、主动接话"),
    LevelDef("亲密", 55, 79, "亲昵随意，会撒娇、记挂对方，偶尔吃醋"),
    LevelDef("挚友", 80, 94, "无话不谈，袒护 TA，情绪上依赖 TA"),
    LevelDef("挚爱", 95, -1, "全身心信任与依恋，把 TA 放在所有人之前"),
]

# 主人专属态度指引默认值：等级框架照用（数值照常涨跌），语义按正负分界——
# 负好感＝闹别扭（不是对外人的厌恶）；正值＝正常正面关系，只是亲疏程度不同。
DEFAULT_MASTER_GUIDANCE: dict[str, str] = {
    "厌恶": "被主人伤透了心在闹大别扭，嘴硬心软、连怼带哭，实际一直盼着被哄——绝不是对外人那种拒之千里",
    "陌生": "有点生分，提不起劲亲昵，但不生气也不冷战——正常说话，就是不黏人，像各忙各的老熟人",
    "认识": "关系不错，温和亲近，相处自在，感情在慢慢升温——还没到特别黏的程度，但已经是能自然说话撒娇的关系",
    "友好": "感情稳定，会撒娇耍赖、跟主人要专属待遇，偶尔耍小性子也理直气壮",
    "亲密": "黏人又霸道，独占欲上来谁都拦不住，随时要关注",
    "挚友": "无话不谈，心事只跟主人说，被护短也护主人",
    "挚爱": "全身心依恋，主人是唯一的例外和软肋，毫无保留",
}

# 等级名 -> 配置键（_conf_schema.json 中 levels 下的键）
_LEVEL_KEYS = {
    "厌恶": "yanwu",
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
                    min_score=float(item.get("min", default.min_score)),
                    max_score=float(item.get("max", default.max_score)),
                    guidance=str(item.get("guidance") or default.guidance),
                    master_guidance=str(
                        item.get("master_guidance")
                        or DEFAULT_MASTER_GUIDANCE[default.name]
                    ),
                )
            )
        return cls(levels)

    def level_of(self, favor: float) -> LevelDef:
        """返回数值对应的等级定义。"""
        result = self._levels[0]
        for lv in self._levels:
            if favor >= lv.min_score:
                result = lv
            else:
                break
        return result

    def guidance_of(self, favor: float, master: bool = False) -> str:
        """返回态度指引：主人且有专属指引时用主人语义版本，否则普通版本。"""
        lv = self.level_of(favor)
        if master and lv.master_guidance:
            return lv.master_guidance
        return lv.guidance

    def all(self) -> list[LevelDef]:
        """返回全部等级（升序）。"""
        return list(self._levels)
