"""心弦好感度 - 评审输出解析（五档制）。

新协议（v1.20）：模型只输出档位标签，分值由配置映射（attitude_deltas），
彻底绕开数字量表偏置（LLM 评审对自由数值会系统性通胀）。
非中性档位必须有「证据:」行（逐字引用），缺失即强制改判中性——
把"先引用证据、再给判断"的门槛做进解析层，提示词规则失效时兜底。

兼容旧协议（态度:友好/敌意/中性 + 分值:±n）：按态度方向映射到
五档窗口内钳制，旧自定义模板/旧模型输出不中断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 五档（顺序固定，与配置 attitude_deltas 的键一一对应）
TIERS = ("敌意", "冷淡", "中性", "友好", "热情")

# 新协议：档位:xx [证据:...] [理由:...]（只匹配本行：空白限定 [ \t]*，防 \s 吞换行跨行匹配）
_TIER_RE = re.compile(r"档位[:：][ \t]*(敌意|冷淡|中性|友好|热情)")
_EVIDENCE_RE = re.compile(r"证据[:：][ \t]*([^\n]*)")
_REASON_RE = re.compile(r"理由[:：][ \t]*([^\n]*)")

# 旧协议：态度:xx 分值:±n
_LEGACY_RE = re.compile(
    r"态度[:：]\s*(友好|善意|敌意|恶意|中性)[\s\S]*?"
    r"分值[:：]\s*([+-]?\d+(?:\.\d+)?)"
)

# 旧三分 → 五档的映射窗口：态度给方向，旧分值在窗口内取值
_LEGACY_WINDOW: dict[str, tuple[str, str]] = {
    "友好": ("友好", "热情"),
    "善意": ("友好", "热情"),
    "敌意": ("敌意", "冷淡"),
    "恶意": ("敌意", "冷淡"),
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
    """解析结果：档位、映射分值（未过经济学层）、证据与理由。"""

    tier: str
    delta: float
    evidence: str = ""
    reason: str = ""


def parse(
    content: str,
    attitude_deltas: dict[str, float] | None = None,
    *,
    max_abs_delta: float = 3.0,
) -> ParsedJudge | None:
    """解析模型输出为新旧协议之一。无法解析返回 None（调用方静默跳过）。

    attitude_deltas: 档位 → 分值映射，缺省回落 DEFAULT_ATTITUDE_DELTAS。
    max_abs_delta: 最终分值的绝对值上限（对映射结果再做一次钳制）。
    """
    deltas = dict(attitude_deltas or DEFAULT_ATTITUDE_DELTAS)
    text = content or ""

    m = _TIER_RE.search(text)
    if m:
        tier = m.group(1)
        ev = _EVIDENCE_RE.search(text)
        reason = _REASON_RE.search(text)
        evidence = (ev.group(1).strip() if ev else "")
        # 证据门槛：非中性档位必须给出非空证据，否则强制改判中性
        if tier != "中性" and not evidence:
            tier = "中性"
        delta = float(deltas.get(tier, deltas.get("中性", 0.0)))
        delta = max(-max_abs_delta, min(max_abs_delta, delta))
        return ParsedJudge(tier=tier, delta=delta, evidence=evidence,
                           reason=(reason.group(1).strip() if reason else ""))

    m = _LEGACY_RE.search(text)
    if m:
        attitude, raw = m.group(1), float(m.group(2))
        if attitude in ("友好", "善意"):
            tier = "热情" if abs(raw) >= deltas.get("热情", 1.8) else "友好"
        elif attitude in ("敌意", "恶意"):
            tier = "敌意" if abs(raw) >= abs(deltas.get("敌意", -2.5)) else "冷淡"
        else:
            tier = "中性"
        delta = max(-max_abs_delta, min(max_abs_delta, float(deltas.get(tier, 0.0))))
        return ParsedJudge(tier=tier, delta=delta)

    return None
