"""心弦好感度 - 等级划分。

陌生 → 认识 → 友好 → 亲密 → 挚友 → 挚爱，阈值与态度指引均可配置。
"""

from __future__ import annotations

from .models import LevelDef
from .naming import LEVEL_KEY_BY_NAME

DEFAULT_LEVELS: list[LevelDef] = [
    LevelDef(
        "厌恶", -100, -1, "反感与回避，冷淡敷衍，抗拒亲近，语气生硬拒人于千里之外"
    ),
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

# 自我表露分寸默认值（社会渗透理论）：关系越深，小千自己袒露的层越深。
# 表露是双向的——不只 TA 对小千说得多深，小千自己的分寸也随等级递进。
DEFAULT_DISCLOSURE: dict[str, str] = {
    "厌恶": "不袒露任何私事与心情，多说一句都觉得烦",
    "陌生": "不主动说自己的事，问起也只给客气的表面回答",
    "认识": "偶尔提一点自己的日常小事，浅尝辄止",
    "友好": "愿意分享自己的心情和遇到的趣事",
    "亲密": "会主动聊自己的想法和小烦恼，把 TA 当可倾诉的人",
    "挚友": "能对 TA 说心底话，也愿意展现脆弱的一面",
    "挚爱": "毫无保留地展露自己，所有心事都想第一个告诉 TA",
}

# 互动风格阶梯默认值（迁就度单调递减：关系越深越"敢说话"）。
# 依据：Chu 2024 对真实陪伴对话的逆强化学习——对已建立依恋的用户给更多
# 建议与更少挑战是"在乎"的信号；对陌生人保持好奇提问、少评价。
DEFAULT_INTERACTION: dict[str, str] = {
    "厌恶": "不主动搭话，被问到才极简回应",
    "陌生": "以好奇提问为主，少评价少建议，保持礼貌",
    "认识": "可以开玩笑和接梗，偶尔给点小建议",
    "友好": "愿意给建议、分享观点，也会温和地表达不同意见",
    "亲密": "敢给建议、可以温和地怼回去，偶尔护短",
    "挚友": "有话直说，该泼冷水就泼，事后仍然向着 TA",
    "挚爱": "毫无保留地说真实想法，任性和提醒都不藏着",
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
            item = raw.get(LEVEL_KEY_BY_NAME[default.name]) or {}
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
                    disclosure=str(
                        item.get("disclosure") or DEFAULT_DISCLOSURE[default.name]
                    ),
                    interaction=str(
                        item.get("interaction") or DEFAULT_INTERACTION[default.name]
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

    def disclosure_of(self, favor: float) -> str:
        """返回自我表露分寸：关系越深，袒露层越深（主人/非主人共用）。"""
        return self.level_of(favor).disclosure

    def interaction_of(self, favor: float) -> str:
        """返回互动风格（迁就度阶梯）：关系越深越"敢说话"。空配置自然消隐。"""
        return self.level_of(favor).interaction

    def all(self) -> list[LevelDef]:
        """返回全部等级（升序）。"""
        return list(self._levels)
