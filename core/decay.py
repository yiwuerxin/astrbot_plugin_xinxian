"""心弦好感度 - 时间衰减（真人记忆规律版）。

研究依据：
- 艾宾浩斯遗忘曲线（Murre & Dros 2015 复现）：记忆按比例衰退、先快后慢；
- 间隔效应（Cepeda 2006/2008）与 SM-2：每次成功复习延长记忆稳定性；
- Half-Life Regression（Duolingo 2016）与 MemoryBank（2023，陪伴机器人）：
  p = 0.5^(Δ/h) 的指数衰减 + 随互动巩固的半衰期，是生产系统的标准形。

公式：effective = baseline + (stored − baseline) × 0.5^(闲置天数 / h)

半衰期 h 的巩固规则（SM-2 温和版）：
- 新关系 h₀ = half_life_base（默认 10 天，每天损约 6.6% 的存量距离）；
- 每次正向互动（delta>0 落库）：h = min(h_max, h × growth)——巩固；
- 中性/负向互动：h 不变（得罪一次不会"忘了你是谁"，SM-2 失败不降 EF）。

stored 值永不因衰减被物理改写（只算 effective 展示值；写路径先结算后叠加，
衰减被锁定）。本模块为纯函数，可单测。
"""

from __future__ import annotations

from .decimal import round1

# 半衰期下限（保险丝）：即使配置被改坏，最快也是每天 ~13% 的比例损失
HALF_LIFE_MIN = 5.0


def effective_favor(
    stored: float,
    updated_at: float,
    now: float,
    *,
    half_life: float,
    baseline: float,
) -> float:
    """指数遗忘：存量距离按 0.5^(闲置天数/h) 衰减，向 baseline 收敛不越界。

    - updated_at <= 0（从未互动）：不衰减。
    - half_life 缺失/非法时回落 HALF_LIFE_MIN。
    - 返回值收敛到一位小数。
    """
    h = float(half_life) if half_life and half_life > 0 else HALF_LIFE_MIN
    if updated_at <= 0 or now <= updated_at:
        return round1(stored)
    idle_days = (now - updated_at) / 86400.0
    factor = 0.5 ** (idle_days / h)
    eff = baseline + (stored - baseline) * factor
    # 不越过 baseline（stored 在两侧时均收敛向它）
    if stored > baseline:
        eff = max(baseline, eff)
    elif stored < baseline:
        eff = min(baseline, eff)
    return round1(eff)


def consolidate_half_life(
    h: float,
    *,
    base: float = 10.0,
    growth: float = 1.3,
    h_max: float = 60.0,
    positive: bool,
) -> float:
    """互动后结算新的半衰期（巩固规则）。

    - 正向互动：h = min(h_max, max(h, base) × growth)（首次互动从 base 起步）；
    - 非正向互动：h 原样返回（但调用方仍会刷新 updated_at 时间锚）；
    - h 非法（<=0）时回落 base。收敛到一位小数。
    """
    cur = float(h) if h and h > 0 else float(base)
    if not positive:
        return round1(cur)
    return round1(min(float(h_max), cur * float(growth)))
