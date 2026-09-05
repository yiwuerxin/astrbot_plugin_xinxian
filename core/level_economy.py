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
from .naming import LEVEL_NAME_BY_KEY

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

# 手感预设（economy.preset）：参数组合打包，显式配置覆盖 preset 值。
# galgame＝易升难降（努力有回报，怎么聊都涨）；realistic＝难升易降
# （好感金贵，得罪一次疼很久）。repair_scale_* 不进预设（等级调制
# 是独立机制，随 preset 联动会混淆两个概念）。
PRESETS: dict[str, dict] = {
    "default": {
        "noise_floor": 0.5, "negative_weight": 1.5, "same_day_decay": 0.25,
        "level_mult": dict(DEFAULT_LEVEL_MULT),
        "repair_threshold": 2.0, "repair_hours": 48.0, "repair_factor": 0.5,
    },
    "galgame": {
        "noise_floor": 0.3, "negative_weight": 1.2, "same_day_decay": 0.15,
        "level_mult": {
            "厌恶": 1.0, "陌生": 1.0, "认识": 1.0,
            "友好": 0.9, "亲密": 0.75, "挚友": 0.55, "挚爱": 0.4,
        },
        "repair_threshold": 2.5, "repair_hours": 36.0, "repair_factor": 0.7,
    },
    "realistic": {
        "noise_floor": 0.6, "negative_weight": 1.8, "same_day_decay": 0.35,
        "level_mult": {
            "厌恶": 1.0, "陌生": 1.0, "认识": 0.9,
            "友好": 0.6, "亲密": 0.4, "挚友": 0.25, "挚爱": 0.15,
        },
        "repair_threshold": 1.8, "repair_hours": 72.0, "repair_factor": 0.35,
    },
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
        repair_threshold: 信任修复期触发阈值——评审分值 ≤ -threshold 的重大
            得罪后进入修复期（信任研究：摧毁快、修复慢）。0=关闭。
        repair_hours: 修复期时长（小时）：窗口内正向分值被压制。
        repair_factor: 修复期内正分乘数（0.5=半效涨回）。1=不压制。
        repair_scale_high: 等级调制上限——深关系（亲密及以上）冒犯时，
            修复期时长与压制强度按此缩放（Dirks 2011：高信任中的冒犯
            更难修复）。1=不缩放（回到旧版统一 48h 行为）。
        repair_scale_low: 浅关系（友好及以下）冒犯的缩放系数，默认 1
            （保持旧版行为）。
    """

    noise_floor: float = 0.5
    negative_weight: float = 1.5
    same_day_decay: float = 0.25
    level_mult: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LEVEL_MULT))
    repair_threshold: float = 2.0
    repair_hours: float = 48.0
    repair_factor: float = 0.5
    repair_scale_high: float = 1.5
    repair_scale_low: float = 1.0

    @classmethod
    def from_config(cls, cfg: dict | None) -> "EconomyConfig | None":
        """从 economy.* 配置构建；缺省回落默认值。enabled=false 时返回 None。

        preset 先垫底（default/galgame/realistic 三档手感预设），显式给出
        的具体参数覆盖 preset 值——一键切手感，细调仍可逐参数覆盖。
        repair_scale_* 不在预设内（等级调制独立配置）。
        level_mult 的配置键为等级拼音（与 _conf_schema.json 一致），
        这里统一翻成中文等级名存储（apply 按等级名查表）。
        """
        raw = cfg or {}
        if not bool(raw.get("enabled", True)):
            return None
        preset_name = str(raw.get("preset", "default") or "default").strip().lower()
        preset = PRESETS.get(preset_name, PRESETS["default"])
        mult_raw = raw.get("level_mult") or {}
        mult = dict(preset["level_mult"])
        for key, name in LEVEL_NAME_BY_KEY.items():
            if key in mult_raw:
                mult[name] = float(mult_raw[key])
            elif name in mult_raw:  # 容错：直接给中文键也认
                mult[name] = float(mult_raw[name])
        return cls(
            noise_floor=float(raw.get("noise_floor", preset["noise_floor"])),
            negative_weight=float(raw.get("negative_weight", preset["negative_weight"])),
            same_day_decay=float(raw.get("same_day_decay", preset["same_day_decay"])),
            level_mult=mult,
            repair_threshold=float(raw.get("repair_threshold", preset["repair_threshold"])),
            repair_hours=float(raw.get("repair_hours", preset["repair_hours"])),
            repair_factor=float(raw.get("repair_factor", preset["repair_factor"])),
            repair_scale_high=max(1.0, float(raw.get("repair_scale_high", 1.5))),
            repair_scale_low=max(1.0, float(raw.get("repair_scale_low", 1.0))),
        )


@dataclass
class EconomyResult:
    """经济学层输出：最终 delta 与各环节标记（供日志/流水核对）。"""

    delta: float
    floored: bool = False      # 被噪声地板归零
    multiplied: bool = False   # 吃了阶段乘数或负面权重
    decayed: bool = False      # 吃了同日重复衰减
    repairing: bool = False    # 处于信任修复期（正分被压制）


def apply(
    delta: float,
    level_name: str,
    positive_today: int = 0,
    cfg: EconomyConfig | None = None,
    repair: bool = False,
) -> EconomyResult:
    """对评审分值跑完整经济学管线。

    顺序：噪声地板 → 负面权重/阶段乘数 → 同日重复衰减 → 修复期压制 → round1。
    delta=0 直接返回（中性评审不产生任何流水）。
    positive_today: 当日已生效的正向评审次数（第 N+1 次吃 N 次衰减）。
    repair: 是否处于信任修复期（重大得罪后的时间窗内），仅压制正分。
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

    # 4. 信任修复期：重大得罪后的时间窗内，正分只算半效（摧毁快、修复慢）
    if d > 0 and repair and cfg.repair_factor != 1:
        d = round1(d * cfg.repair_factor)
        res.repairing = True

    res.delta = d
    return res


# 修复期等级调制的分界：这些等级的冒犯视为"深关系冒犯"（吃 repair_scale_high）
DEEP_LEVELS = frozenset({"亲密", "挚友", "挚爱"})


def repair_params_for_level(
    level_name: str, cfg: "EconomyConfig | None"
) -> tuple[float, float]:
    """按冒犯时的等级返回 (修复期小时数, 修复期正分乘数)。

    信任修复研究（Dirks et al. 2011）：冒犯前的信任水平决定修复难度——
    高信任关系中的冒犯用廉价言语更难修复。深关系（亲密/挚友/挚爱）
    冒犯 → hours × scale_high 且 factor 进一步压深（1-(1-factor)×scale_high）；
    浅关系 → hours × scale_low（默认 1 = 旧版统一行为）。
    cfg 为 None 时返回 (0, 1)（未启用经济层，无修复期）。
    """
    if cfg is None or cfg.repair_threshold <= 0 or cfg.repair_hours <= 0:
        return 0.0, 1.0
    if level_name in DEEP_LEVELS and cfg.repair_scale_high > 1.0:
        hours = cfg.repair_hours * cfg.repair_scale_high
        # factor 压深：0.5、scale 1.5 → 1-0.5*1.5=0.25（更难涨回）
        factor = max(0.0, 1.0 - (1.0 - cfg.repair_factor) * cfg.repair_scale_high)
    else:
        hours = cfg.repair_hours * max(1.0, cfg.repair_scale_low)
        factor = cfg.repair_factor
    return round(hours, 1), round1(factor)
