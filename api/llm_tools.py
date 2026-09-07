"""心弦好感度 - LLM 工具 handler。

由 main.py 中 @filter.llm_tool 装饰的 Star 方法薄壳调用。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..core.decimal import fmt
from ..services.favor_service import FavorService


async def tool_query_favor(
    svc: FavorService, event: AstrMessageEvent, target: str = ""
) -> str:
    """查询某成员对小千的好感度；target 为空时查发言人自己。"""
    group_id = event.get_group_id()
    user_id = (target or "").strip() or event.get_sender_id()
    rec = await svc.get(group_id, user_id)
    lv = svc.level_of(rec.favor)
    master = "，是小千的主人" if svc.is_master(user_id) else ""
    rel = (
        f"，是小千的{svc.relationship_label(rec.relationship)}"
        if rec.relationship
        else ""
    )
    return f"QQ {user_id}{master}{rel} 在本群对小千的好感度为 {fmt(rec.favor)}（{lv.name}）。"


async def tool_query_ranking(
    svc: FavorService, event: AstrMessageEvent, limit: int = 10
) -> str:
    """查询本群好感度排行。"""
    group_id = event.get_group_id()
    rows = await svc.ranking(group_id, limit)
    if not rows:
        return "本群还没有好感度记录。"
    lines = [
        f"{i + 1}. QQ {r.user_id}：{fmt(r.favor)}（{svc.level_of(r.favor).name}）"
        for i, r in enumerate(rows)
    ]
    return "本群对小千的好感度排行：\n" + "\n".join(lines)
