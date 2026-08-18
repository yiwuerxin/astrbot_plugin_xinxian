"""心弦好感度 - 评估提示词纯渲染。

judge 模板占位符：{text}（原话）、{persona_name}（当前人格称呼）、
{persona_block}（人设摘要段，空人设渲染为空串）、{roster}（群成员花名册段，
空花名册渲染为空串）。人格由 services.judge_service 随会话动态解析
（AstrBot 人格切换后同步），本模块只做无副作用的字符串组装，保持可单测。
"""

from __future__ import annotations

_PERSONA_SNIPPET_MAX = 500


def persona_block(persona_prompt: str, max_chars: int = _PERSONA_SNIPPET_MAX) -> str:
    """人设 prompt → 模板引用的摘要段；空返回空串；超长截断加省略号。"""
    p = (persona_prompt or "").strip()
    if not p:
        return ""
    if len(p) > max_chars:
        p = p[:max_chars].rstrip() + "…"
    return f"（人设摘要，供理解语境）：\n{p}\n\n"


def roster_block(roster: str) -> str:
    """花名册配置 → 模板引用段；空返回空串。

    roster 是自由文本（如「阿狸=123456789（某群友的外号）」逐行/分号分隔），
    原样透传给模板，让评审模型知道外号↔QQ 的映射与群成员常识。
    """
    r = (roster or "").strip()
    if not r:
        return ""
    return f"群成员花名册（仅供理解谁是谁，不代表说话人态度）：\n{r}\n\n"


def render(
    template: str, *, text: str, persona_name: str, persona_prompt: str = "",
    roster: str = "",
) -> str:
    """渲染评估模板。format 忽略模板未引用的占位符，
    旧的自定义模板（只含 {text}）无需改动即可继续使用。"""
    return template.format(
        text=text,
        persona_name=(persona_name or "").strip() or "小千",
        persona_block=persona_block(persona_prompt),
        roster=roster_block(roster),
    )
