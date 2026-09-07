"""心弦好感度 - 评审输出解析（五档 + 模型自由分值）。

协议：模型输出 档位 + 分值（模型自己给分，分值是评审的主判断），
档位作为锚定与校验：分值方向必须与档位一致、幅度须落在该档的
区间内（区间由 attitude_deltas 的相邻档分值构成），不一致时钳制。
非中性档位必须有「证据:」行（逐字引用），缺失即强制改判中性。

防通胀不靠压缩模型输出，而靠 economy 层（噪声地板/乘数/衰减）。
兼容旧协议（态度:xx 分值:±n）：态度映射档位，分值照用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 五档（顺序固定；attitude_deltas 键即档位名）
TIERS = ("敌意", "冷淡", "中性", "友好", "热情")

# 新协议：档位:xx / 分值:±n / 证据:... / 理由:...（各字段只匹配本行）
_TIER_RE = re.compile(r"档位[:：][ \t]*(敌意|冷淡|中性|友好|热情)")
_SCORE_RE = re.compile(r"分值[:：][ \t]*([+-]?\d+(?:\.\d+)?)")
_EVIDENCE_RE = re.compile(r"证据[:：][ \t]*([^\n]*)")
_REASON_RE = re.compile(r"理由[:：][ \t]*([^\n]*)")

# 旧协议：态度:xx 分值:±n
_LEGACY_RE = re.compile(
    r"态度[:：]\s*(友好|善意|敌意|恶意|中性)[\s\S]*?"
    r"分值[:：]\s*([+-]?\d+(?:\.\d+)?)"
)
_LEGACY_TIER = {
    "友好": "友好",
    "善意": "友好",
    "敌意": "敌意",
    "恶意": "敌意",
    "中性": "中性",
}

DEFAULT_ATTITUDE_DELTAS: dict[str, float] = {
    "敌意": -2.5,
    "冷淡": -0.8,
    "中性": 0.0,
    "友好": 0.6,
    "热情": 1.8,
}


@dataclass
class ParsedJudge:
    """解析结果：档位、模型给的分值（已按档位区间钳制）、证据与理由。"""

    tier: str
    delta: float
    evidence: str = ""
    reason: str = ""


def _clamp_to_tier(score: float, tier: str, deltas: dict[str, float]) -> float:
    """把模型分值钳制到其宣称档位的区间内（区间与提示词中的给分规则一致）。

    各档自身锚点构成边界：敌意 [敌意锚, 冷淡锚) / 冷淡 [冷淡锚, 0) /
    中性 = 0 / 友好 (0, 友好锚] / 热情 [热情锚, 敌意锚绝对值上限]。
    分值方向与档位矛盾（如敌意档给正分）时，以档位锚点为准。
    """
    if tier == "中性":
        return 0.0
    d = deltas.get(tier, 0.0)
    if tier == "敌意":
        lo = d  # 敌意锚（如 -2.5）为下界
        hi = deltas.get("冷淡", -0.8)  # 冷淡锚为上界
        return max(lo, min(hi, score)) if score < 0 else lo
    if tier == "冷淡":
        lo = d  # 冷淡锚（如 -0.8）为下界
        hi = 0.0
        return max(lo, min(hi, score)) if score < 0 else lo
    if tier == "友好":
        hi = d  # 友好锚（如 0.6）为上界
        return min(hi, max(0.1, score)) if score > 0 else hi
    # 热情：热情锚（如 1.8）为下界，上界交给 max_abs_delta 兜底
    return max(d, score) if score > 0 else d


def parse(
    content: str,
    attitude_deltas: dict[str, float] | None = None,
    *,
    max_abs_delta: float = 3.0,
) -> ParsedJudge | None:
    """解析模型输出为新旧协议之一。无法解析返回 None（调用方静默跳过）。

    attitude_deltas: 档位标称分值（构成各档钳制区间）。
    max_abs_delta: 分值绝对值上限。
    """
    deltas = dict(attitude_deltas or DEFAULT_ATTITUDE_DELTAS)
    text = content or ""

    m = _TIER_RE.search(text)
    if m:
        tier = m.group(1)
        sm = _SCORE_RE.search(text)
        ev = _EVIDENCE_RE.search(text)
        reason = _REASON_RE.search(text)
        evidence = ev.group(1).strip() if ev else ""
        # 证据门槛：非中性档位必须给出非空证据，否则强制改判中性
        if tier != "中性" and not evidence:
            tier = "中性"
        if tier == "中性":
            return ParsedJudge(
                tier="中性",
                delta=0.0,
                evidence="",
                reason=(reason.group(1).strip() if reason else ""),
            )
        score = float(sm.group(1)) if sm else deltas.get(tier, 0.0)
        score = _clamp_to_tier(score, tier, deltas)
        score = max(-max_abs_delta, min(max_abs_delta, score))
        return ParsedJudge(
            tier=tier,
            delta=score,
            evidence=evidence,
            reason=(reason.group(1).strip() if reason else ""),
        )

    m = _LEGACY_RE.search(text)
    if m:
        attitude, score = m.group(1), float(m.group(2))
        tier = _LEGACY_TIER.get(attitude, "中性")
        if tier == "中性":
            return ParsedJudge(tier="中性", delta=0.0)
        score = _clamp_to_tier(score, tier, deltas)
        score = max(-max_abs_delta, min(max_abs_delta, score))
        return ParsedJudge(tier=tier, delta=score)

    return None
