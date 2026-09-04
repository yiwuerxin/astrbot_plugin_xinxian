"""心弦好感度 - 数据模型。

纯数据类，承载好感度记录、等级定义与变化结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class FavorRecord:
    """一条好感度记录（群 + 人 两个维度定位）。

    Attributes:
        group_id: 群号。
        user_id: 成员 QQ 号。
        favor: 当前好感度（min_favor..max_favor，可为负；精度一位小数）。
        updated_at: 最近变动时间（epoch 秒，0 表示从未变动）。
    """

    group_id: str
    user_id: str
    favor: float = 0.0
    updated_at: float = 0.0
    relationship: str = ""
    nickname: str = ""
    impression: str = ""
    tags: str = ""          # JSON 数组字符串（如 '["毒舌","夜猫子"]'）
    impression_at: float = 0.0
    half_life: float = 10.0  # 遗忘曲线半衰期（天）；正互动巩固增长

    def parsed_tags(self) -> list[str]:
        """tags JSON 字符串 → 标签列表；损坏/空返回 []。"""
        if not self.tags:
            return []
        try:
            v = json.loads(self.tags)
            return [str(t) for t in v] if isinstance(v, list) else []
        except Exception:
            return []


@dataclass
class LevelDef:
    """好感度等级定义。

    Attributes:
        name: 等级名（厌恶/陌生/认识/友好/亲密/挚友/挚爱）。
        min_score: 进入该等级的最低分（含）。
        max_score: 该等级最高分（含），-1 表示无上限。
        guidance: 注入提示词用的态度指引文本。
        master_guidance: 主人专属态度指引；空串表示无，回落普通 guidance。
        disclosure: 自我表露分寸（社会渗透理论：关系越深，袒露的层越深）。
        interaction: 互动风格（迁就度阶梯：关系越深越"敢说话"——提问→
            建议→温和反驳→有话直说）。空串表示未配置。
    """

    name: str
    min_score: float
    max_score: float
    guidance: str
    master_guidance: str = ""
    disclosure: str = ""
    interaction: str = ""


@dataclass
class FavorChange:
    """一次好感度变化的结果。

    Attributes:
        delta: 实际生效的变化量（经过冷却与每日上限截断后）。
        reason: 变化原因（事件名或 judge:态度）。
        source: 来源标识：judge / admin / api / undo 等。
        clamped: 是否被冷却或每日上限截断过。
        favor_after: 变化后的好感度。
    """

    delta: float
    reason: str
    source: str
    clamped: bool = False
    favor_after: float = 0.0
