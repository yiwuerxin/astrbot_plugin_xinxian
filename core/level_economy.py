"""心弦好感度 - 防通胀经济学层（仅评审路径）。

依据（研究综述）：
- 曝光效应是减速曲线甚至倒 U：重复寒暄边际增益趋零 → 同日重复衰减；
- 社会渗透理论：浅层互动不能兑换深层进度 → 高阶段正分乘数递减；
- 负性偏向（坏事比好事重 2~5 倍，Gottman 5:1）→ 负面权重放大；
- 评审宽大偏置 → 噪声地板消灭碎分。

规则引擎（daily_first）与跨插件 API 不经过本层（保持直给语义）。
所有出口 round1 收敛（core.decimal 浮点纪律）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decimal import round1

# 各等级的默认正分乘数（键 = LevelTable 的等级名；仅正分生效，负分不吃）
DEFAULT_LEVEL_MULT: dict[str, float] = {
    "厌恶": 1.0,
    "陌生": 1.0,
    "认识": 1.0,
    "友好": 0.75,
    "亲密": 0.55,
    "挚友": 0.35,
    "挚爱": 0.2,
}


@dataclass
class EconomyConfig:
    """经济学层配置（由 FavorService 持有，main.py 从 economy.* 装配）。

    Attributes:
        noise_floor: 噪声地板，|delta| 小于该值直接归零（消灭评审碎分）。0=关闭。
        negative_weight: 负面权重放大倍数（负性偏向）。1=不放大。
        same_day_decay: 同日重复衰减斜率：当日已有 N 次正向评审时，
            第 N+1 次正分乘 max(0.25, 1 - slope*N)。0=关闭。
        level_mult: 等级名 → 正分乘数（社会渗透：高阶段寒暄不再推进）。
    """

    noise_floor: float = 0.5
    negative_weight: float = 1.5
    same_day_decay: float = 0.25
    level_mult: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LEVEL_MULT))

    @classmethod
    def from_config(cls, cfg: dict | None) -> "EconomyConfig | None":
        """从 economy.* 配置构建；缺省回落默认值。enabled=false 时返回 None。

        level_mult 的配置键为等级拼音（与 _conf_schema.json 一致），
        这里统一翻成中文等级名存储（apply 按等级名查表）。
        """
        raw = cfg or {}
        if not bool(raw.get("enabled", True)):
            return None
        _PINYIN = {
            "yanwu": "厌恶", "mosheng": "陌生", "renshi": "认识", "youhao": "友好",
            "qinmi": "亲密", "zhiyou": "挚友", "zhiai": "挚爱",
        }
        mult_raw = raw.get("level_mult") or {}
        mult = dict(DEFAULT_LEVEL_MULT)
        for key, name in _PINYIN.items():
            if key in mult_raw:
                mult[name] = float(mult_raw[key])
            elif name in mult_raw:  # 容错：直接给中文键也认
                mult[name] = float(mult_raw[name])
        return cls(
            noise_floor=float(raw.get("noise_floor", 0.5)),
            negative_weight=float(raw.get("negative_weight", 1.5)),
            same_day_decay=float(raw.get("same_day_decay", 0.25)),
            level_mult=mult,
        )


@dataclass
class EconomyResult:
    """经济学层输出：最终 delta 与各环节标记（供日志/流水核对）。"""

    delta: float
    floored: bool = False      # 被噪声地板归零
    multiplied: bool = False   # 吃了阶段乘数或负面权重
    decayed: bool = False      # 吃了同日重复衰减


def apply(
    delta: float,
    level_name: str,
    positive_today: int = 0,
    cfg: EconomyConfig | None = None,
) -> EconomyResult:
    """对评审分值跑完整经济学管线。

    顺序：噪声地板 → 负面权重/阶段乘数 → 同日重复衰减 → round1。
    delta=0 直接返回（中性评审不产生任何流水）。
    positive_today: 当日已生效的正向评审次数（第 N+1 次吃 N 次衰减）。
    """
    if cfg is None:
        return EconomyResult(delta=round1(delta))
    d = round1(delta)
    if d == 0:
        return EconomyResult(delta=0.0)

    res = EconomyResult(delta=0.0)

    # 1. 噪声地板：碎分归零（评审宽大偏置的兜底拦截）
    if cfg.noise_floor > 0 and abs(d) < cfg.noise_floor:
        return EconomyResult(delta=0.0, floored=True)
    d = round1(d)

    # 2. 负面权重 / 阶段乘数（二选一，按方向）
    if d < 0:
        if cfg.negative_weight != 1:
            d = round1(d * cfg.negative_weight)
            res.multiplied = True
    else:
        mult = float(cfg.level_mult.get(level_name, 1.0))
        if mult != 1:
            d = round1(d * mult)
            res.multiplied = True

    # 3. 同日重复衰减：仅正分（防连刷；曝光效应倒 U）
    if d > 0 and cfg.same_day_decay > 0 and positive_today > 0:
        factor = max(0.25, 1 - cfg.same_day_decay * positive_today)
        if factor < 1:
            d = round1(d * factor)
            res.decayed = True

    res.delta = d
    return res
