"""印象带权点模型（P-C，GOAL Phase 3）。

把"一句话印象"升级为带权印象点集：LLM 提 (point, weight 1-10)；
相似点合并（SequenceMatcher ≥ 0.6，权重求和、保留最长描述）；活跃点
>10 时按 weight × 时间权重加权随机保留 10 条，挤出项并入长印象。
时间权重阶梯（GOAL 规格，非单调）：1h=1.0 → 24h=0.7 → 7d=0.95 →
30d=0.1（更早 0.05）——一小时内最新鲜、一天内让位、一周内回稳（长期
未翻旧账的点重新有分量）、一个月后近乎遗忘。好感负向变化时新点权重
×1.5（损失厌恶）。分析提示词把其他群友昵称替换为「用户A/B」防串像。
纯函数可单测；存储为 favor.points（schema v9，JSON 数组字符串）。
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

MERGE_SIMILARITY = 0.6
MAX_ACTIVE_POINTS = 10
LOSS_AVERSION = 1.5

POINTS_PROMPT = """你是「{persona_name}」的印象管理员。根据下面群成员「{who}」的互动记录，提取 3~6 条关于 TA 的印象点。

{old_points}最近互动记录（增减分 | 发言 | 当时的判断）：
{samples}

要求：
- 每条印象点：一句具体描述（这个人的性格/说话风格/和你的关系感觉）+ 权重 1~10（越核心越重）
- 记录里出现的其他群友一律只称「用户A」「用户B」（按出现顺序编号），不要写他们的名字
- 负面得罪（增减分明显为负的记录）对应的印象点权重可以给高些

只输出 JSON 数组，不要其他内容：
[{{"point": "印象描述", "weight": 5}}, ...]"""


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a or "", b or "").ratio()


def merge_points(existing: list[dict], new: list[dict],
                 similarity: float = MERGE_SIMILARITY) -> list[dict]:
    """相似点合并：权重求和、保留最长描述；不相似则追加。"""
    out = [dict(p) for p in (existing or [])]
    for p in (new or []):
        text = str(p.get("point") or "").strip()
        if not text:
            continue
        weight = max(1, min(10, int(float(p.get("weight") or 5))))
        hit = next((q for q in out if _similarity(str(q.get("point") or ""), text) >= similarity), None)
        if hit is not None:
            hit["weight"] = max(1, min(99, int(hit.get("weight") or 1) + weight))
            if len(text) > len(str(hit.get("point") or "")):
                hit["point"] = text
        else:
            out.append({"point": text, "weight": weight})
    return out


def time_weight(age_seconds: float) -> float:
    """时间权重阶梯（GOAL 规格的非单调阶梯）。"""
    age = max(0.0, float(age_seconds or 0))
    if age < 3600:
        return 1.0
    if age < 86400:
        return 0.7
    if age < 7 * 86400:
        return 0.95
    if age < 30 * 86400:
        return 0.1
    return 0.05


def retain(points: list[dict], now: float, cap: int = MAX_ACTIVE_POINTS,
           rng=None) -> tuple[list[dict], list[dict]]:
    """活跃点 > cap 时按 weight × time_weight 加权随机保留 cap 条。

    返回 (保留, 挤出)。挤出项由调用方并入长印象文本。
    rng 可注入 random.Random(seed) 供测试确定性。
    """
    import random

    pts = [dict(p) for p in (points or [])]
    if len(pts) <= cap:
        return pts, []
    if rng is None:
        rng = random.Random()
    weights = [max(0.0001, float(p.get("weight") or 1) * time_weight(now - float(p.get("ts") or 0)))
               for p in pts]
    keep_idx: set[int] = set()
    idx_pool = list(range(len(pts)))
    while len(keep_idx) < cap and idx_pool:
        chosen = rng.choices(idx_pool, weights=[weights[i] for i in idx_pool], k=1)[0]
        keep_idx.add(chosen)
        idx_pool.remove(chosen)
    kept = [pts[i] for i in sorted(keep_idx)]
    dropped = [p for i, p in enumerate(pts) if i not in keep_idx]
    return kept, dropped


def loss_aversion_multiplier(favor_delta: float) -> float:
    """好感负向变化 ×1.5（损失厌恶：坏事记得更牢）。"""
    return LOSS_AVERSION if float(favor_delta or 0) < 0 else 1.0


def anonymize(text: str, nicknames: list[str]) -> str:
    """把文本里出现的其他群友昵称替换为 用户A/B/C…（按在文本中的出现顺序编号）。"""
    t = str(text or "")
    nicks = {str(n).strip() for n in (nicknames or []) if str(n).strip()}
    hits = sorted([(t.find(n), len(n), n) for n in nicks if n and n in t])
    out = t
    for order, (_, _, nick) in enumerate(hits):
        out = out.replace(nick, f"用户{chr(ord('A') + order)}")
    return out


def parse_points(raw: str) -> list[dict] | None:
    """解析 LLM 输出 [{"point":…,"weight":…}]；失败返回 None。"""
    text = str(raw or "").strip()
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        if not isinstance(arr, list):
            return None
        pts = [{"point": str(it.get("point") or "").strip()[:80],
                "weight": max(1, min(10, int(float(it.get("weight") or 5))))}
               for it in arr if isinstance(it, dict) and str(it.get("point") or "").strip()]
        return pts or None
    except Exception:
        return None


def render_impression(points: list[dict], limit: int = 80) -> str:
    """活跃点 → 一句话印象（权重降序前 3 条拼接，≤limit 字）。"""
    top = sorted((p for p in (points or []) if p.get("point")),
                 key=lambda p: -int(p.get("weight") or 0))[:3]
    if not top:
        return ""
    return "；".join(str(p["point"]) for p in top)[:limit]
