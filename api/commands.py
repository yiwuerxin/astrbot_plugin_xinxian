"""心弦好感度 - 聊天指令 handler。

由 main.py 中 @filter.command 装饰的 Star 方法薄壳调用。
查询类指令所有成员可用；设置/重置类指令在 main.py 处以管理员权限注册。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..core.decimal import fmt
from ..services.favor_service import FavorService
from . import llm_tools


async def handle_query(svc: FavorService, event: AstrMessageEvent) -> str:
    """/好感度 —— 查自己。"""
    return await llm_tools.tool_query_favor(svc, event, "")


async def handle_ranking(svc: FavorService, event: AstrMessageEvent, limit: int = 10) -> str:
    """/好感排行 —— 查本群榜单。"""
    return await llm_tools.tool_query_ranking(svc, event, limit)


async def handle_set(
    svc: FavorService, event: AstrMessageEvent, target: str, value: float
) -> str:
    """/好感设置 <QQ号> <数值>（管理员，数值支持一位小数与负值）。"""
    target = (target or "").strip()
    if not target.isdigit():
        return "用法：/好感设置 QQ号 数值"
    rec = await svc.set_favor(event.get_group_id(), target, value)
    return f"已将 QQ {target} 在本群的好感度设置为 {fmt(rec.favor)}。"


async def handle_reset(svc: FavorService, event: AstrMessageEvent, target: str = "") -> str:
    """/好感重置 [QQ号]（管理员）。不带参数清空整群。"""
    group_id = event.get_group_id()
    target = (target or "").strip()
    if target:
        if not target.isdigit():
            return "用法：/好感重置 [QQ号]"
        await svc.reset(group_id, target)
        return f"已重置 QQ {target} 在本群的好感度。"
    await svc.reset(group_id, None)
    return "已重置本群全部好感度数据。"


async def handle_set_relationship(
    svc: FavorService, relationships, event: AstrMessageEvent, target: str, key: str
) -> str:
    """/关系设置 QQ号 类型（管理员；类型留空=清除）。"""
    target = (target or "").strip()
    key = (key or "").strip()
    if not target.isdigit():
        keys = "、".join(relationships.keys()) if relationships else "任意自定义"
        return f"用法：/关系设置 QQ号 类型（可选：{keys}；留空清除）"
    await svc.set_relationship(event.get_group_id(), target, key)
    if not key:
        return f"已清除 QQ {target} 与小千的关系标签。"
    label = relationships.label_of(key) if relationships else key
    return f"已将 QQ {target} 与小千的关系设为「{label}」。"


async def build_rank_image(
    svc: FavorService, event: AstrMessageEvent, font_path: str = "", rows_per_col: int = 12
) -> str:
    """渲染本群好感度排行为图片（查询人高亮），返回临时 PNG 路径。"""
    rows = await svc.standings(event.get_group_id(), limit=max(rows_per_col * 5, rows_per_col))
    from .rank_image import render_ranking
    return render_ranking(rows, event.get_sender_id(), font_path=font_path, rows_per_col=rows_per_col)


async def try_text_wake(
    svc: FavorService,
    event: AstrMessageEvent,
    query_phrases: set[str],
    ranking_phrases: set[str],
    font_path: str = "",
    rows_per_col: int = 12,
) -> str | None:
    """群聊文字唤醒：命中短语 → 返回排行图片路径；否则 None。带 / 的交由 / 指令。"""
    msg = (event.message_str or "").strip()
    if not msg or msg.startswith("/"):
        return None
    if msg in query_phrases or msg in ranking_phrases:
        return await build_rank_image(svc, event, font_path, rows_per_col)
    return None
