"""心弦好感度 - 时间衰减。

久不互动时，好感度向基线慢慢靠拢（像真人"久不联系就淡了"）。
- 读取时：用 effective_favor 计算并展示"有效值"（get / ranking）。
- 写入时：apply_delta 在锁内先把存量衰减到当下，再叠加本次增减 → 衰减被"锁定"，
  下次互动基于已衰减后的值，不会因一次互动就把长期冷淡抹平。
"""

from __future__ import annotations

from .decimal import round1


def effective_favor(
    stored: float,
    updated_at: float,
    now: float,
    *,
    per_day: float,
    grace_days: float,
    baseline: float,
) -> float:
    """计算衰减后的有效好感度。

    - updated_at <= 0（从未互动）：不衰减。
    - idle 未超过 grace_days：不衰减（宽限期）。
    - 超过 grace_days：每多一天，stored 向 baseline 靠 per_day，但不会越过 baseline。
    返回值收敛到一位小数。
    """
    if not per_day or per_day <= 0 or updated_at <= 0:
        return round1(stored)
    idle = max(0.0, (now - updated_at) / 86400.0)
    if idle <= grace_days:
        return round1(stored)
    shrink = per_day * (idle - grace_days)
    if stored > baseline:
        return round1(max(baseline, stored - shrink))
    if stored < baseline:
        return round1(min(baseline, stored + shrink))
    return round1(stored)
