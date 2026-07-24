"""心弦好感度 - 聊天指令 handler。

由 main.py 中 @filter.command 装饰的 Star 方法薄壳调用。
查询类指令所有成员可用；设置/重置类指令在 main.py 处以管理员权限注册。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ..services.favor_service import FavorService
from . import llm_tools


async def handle_query(svc: FavorService, event: AstrMessageEvent) -> str:
    """/好感度 —— 查自己。"""
    return await llm_tools.tool_query_favor(svc, event, "")


async def handle_ranking(svc: FavorService, event: AstrMessageEvent, limit: int = 10) -> str:
    """/好感排行 —— 查本群榜单。"""
    return await llm_tools.tool_query_ranking(svc, event, limit)


async def handle_set(svc: FavorService, event: AstrMessageEvent, target: str, value: int) -> str:
    """/好感设置 <QQ号> <数值>（管理员）。"""
    target = (target or "").strip()
    if not target.isdigit():
        return "用法：/好感设置 QQ号 数值"
    rec = await svc.set_favor(event.get_group_id(), target, value)
    return f"已将 QQ {target} 在本群的好感度设置为 {rec.favor}。"


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
