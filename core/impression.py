"""心弦好感度 - 成员印象与标签（纯逻辑）。

印象 = 一句话的人物侧写（LLM 从评审流水汇总，≤80 字）；
标签 = 0~3 个短词（LLM 给主观标签，stats_tags 给确定性统计标签兜底）。
注入对话（让小千"记得"每个人是谁）并展示在 WebUI。
"""

from __future__ import annotations

import re
import time

IMPRESSION_MAX = 80        # 印象正文长度上限
TAG_MAX = 3                # 标签数量上限
TAG_LEN_MAX = 6            # 单个标签长度上限

_SUMMARY_RE = re.compile(r"印象[:：][ \t]*([^\n]+)")
_TAGS_RE = re.compile(r"标签[:：][ \t]*([^\n]+)")


def stats_tags(logs: list[dict]) -> list[str]:
    """从评审流水统计出确定性标签（LLM 标签的兜底/补充）。

    logs: query_logs 返回的 dict 列表（含 delta/ts/source）。
    规则：常客（≥10 条）、夜猫子（30% 互动在 0-6 点）、
    热情（正评占比 >70% 且 ≥3 条）、毒舌（负评占比 >40% 且 ≥3 条）。
    """
    judged = [r for r in (logs or []) if r.get("source") == "judge"]
    if not judged:
        return []
    tags: list[str] = []
    if len(judged) >= 10:
        tags.append("常客")
    night = sum(
        1 for r in judged
        if 0 <= time.localtime(float(r.get("ts") or 0)).tm_hour < 6
    )
    if night / len(judged) >= 0.3:
        tags.append("夜猫子")
    pos = sum(1 for r in judged if float(r.get("delta") or 0) > 0)
    neg = sum(1 for r in judged if float(r.get("delta") or 0) < 0)
    if pos >= 3 and pos / len(judged) > 0.7:
        tags.append("热情")
    if neg >= 3 and neg / len(judged) > 0.4:
        tags.append("毒舌")
    return tags


def build_summary_prompt(
    nickname: str, old_impression: str, samples: list[str],
    persona_name: str = "小千",
) -> str:
    """构造印象汇总提示词（走一次廉价 LLM 调用）。

    samples: 最近评审记录摘要行（如「+0.8 你好可爱啊 —— 夸我可爱」），时间正序。
    """
    who = (nickname or "").strip() or "该成员"
    old = (old_impression or "").strip()
    old_line = f"旧印象（可延续可推翻）：{old}\n" if old else ""
    body = "\n".join(samples[-20:]) or "（暂无记录）"
    return (
        f"你是「{persona_name}」的印象管理员。根据下面的互动记录，"
        f"用{persona_name}的视角给群成员「{who}」写一句印象侧写，并给几个标签。\n\n"
        f"{old_line}最近互动记录（增减分 | 发言 | 当时的判断）：\n{body}\n\n"
        "要求：\n"
        f"- 印象一句话、不超过{IMPRESSION_MAX}字，写这个人的性格/说话风格/和你的关系感觉，"
        "要有具体感（如「嘴硬心软，爱用外号逗人」），不要空话\n"
        "- 如果这个人前后有了变化，用「以前觉得……，最近……」的双段式写清来龙去脉，"
        "保留变化的轨迹（如「以前爱抬杠，上周深聊后其实挺温和」）；没有变化就延续或润色原有判断\n"
        f"- 标签 {TAG_MAX} 个以内、每个不超过{TAG_LEN_MAX}字（如：毒舌、夜猫子、自来熟）"
        "——标签反映现在的 TA\n"
        "只输出两行，不要任何其他内容：\n"
        "印象:…\n"
        "标签:a,b,c（没有合适标签就写「标签:无」）"
    )


def parse_summary(content: str) -> tuple[str, list[str]] | None:
    """解析 LLM 汇总输出「印象:…\n标签:a,b,c」。

    印象截断到 IMPRESSION_MAX 字；标签最多 TAG_MAX 个、单个截断到
    TAG_LEN_MAX 字、去掉空项与重复；解析失败返回 None（调用方静默跳过）。
    """
    text = content or ""
    m = _SUMMARY_RE.search(text)
    t = _TAGS_RE.search(text)
    if not m:
        return None
    impression = m.group(1).strip()[:IMPRESSION_MAX]
    if not impression:
        return None
    raw_tags = (t.group(1).strip() if t else "").replace("，", ",")
    tags: list[str] = []
    for part in raw_tags.split(","):
        p = part.strip()[:TAG_LEN_MAX]
        if p and p != "无" and p not in tags:
            tags.append(p)
        if len(tags) >= TAG_MAX:
            break
    return impression, tags
