"""心弦好感度 - 跨插件稳定 API。

其他插件获取方式（AstrBot 跨插件调用惯例）：

    star = context.get_registered_star("astrbot_plugin_xinxian")
    api = star.star_cls.api          # 即本类的实例
    favor = await api.get_favor(group_id, user_id)

本接口承诺向后兼容：只增不改。扩展能力请新增方法。
"""

from __future__ import annotations

from ..services.favor_service import FavorService


class XinxianFacade:
    """心弦对外 API 门面。"""

    def __init__(self, favor: FavorService) -> None:
        self._favor = favor

    async def get_favor(self, group_id: str, user_id: str) -> float:
        """查询好感度数值（精度一位小数，可为负）。无记录返回初始值。"""
        rec = await self._favor.get(group_id, user_id)
        return rec.favor

    async def get_level(self, group_id: str, user_id: str) -> dict:
        """查询完整等级信息。

        Returns:
            {"favor": float, "level": str, "guidance": str, "is_master": bool}
        """
        rec = await self._favor.get(group_id, user_id)
        lv = self._favor.level_of(rec.favor)
        return {
            "favor": rec.favor,
            "level": lv.name,
            "guidance": lv.guidance,
            "is_master": self._favor.is_master(user_id),
        }

    async def add_favor(
        self, group_id: str, user_id: str, delta: float, reason: str = "api"
    ) -> float:
        """增减好感度（受每日限幅），返回变化后的数值。"""
        change = await self._favor.change(group_id, user_id, delta, reason=reason)
        return change.favor_after

    async def set_favor(self, group_id: str, user_id: str, value: float) -> float:
        """直接设定好感度，返回设定后的数值。"""
        rec = await self._favor.set_favor(group_id, user_id, value)
        return rec.favor

    async def get_ranking(self, group_id: str, limit: int = 10) -> list[dict]:
        """群内好感度排行（降序）。"""
        rows = await self._favor.ranking(group_id, limit)
        return [
            {
                "user_id": r.user_id,
                "favor": r.favor,
                "level": self._favor.level_of(r.favor).name,
            }
            for r in rows
        ]

    def is_master(self, user_id: str) -> bool:
        """判断是否为主人（按 QQ 号）。"""
        return self._favor.is_master(user_id)

    async def get_relationship(self, group_id: str, user_id: str) -> str:
        """查询关系类型标签（原始 key，未设置返回空串）。"""
        rec = await self._favor.get(group_id, user_id)
        return rec.relationship or ""

    async def set_relationship(self, group_id: str, user_id: str, key: str) -> str:
        """设定关系类型标签，返回展示名。不影响好感度数值。"""
        await self._favor.set_relationship(group_id, user_id, key)
        return self._favor.relationship_label(key)

    async def get_profile(self, group_id: str, user_id: str) -> dict:
        """完整好感画像（P-H 跨插件联动，maisoul 等渲染进系统提示词用）。

        Returns:
            {"favor", "level", "guidance", "impression", "tags",
             "relationship", "is_master"}
        """
        rec = await self._favor.get(group_id, user_id)
        lv = self._favor.level_of(rec.favor)
        return {
            "favor": rec.favor,
            "level": lv.name,
            "guidance": lv.guidance,
            "impression": (rec.impression or "").strip(),
            "tags": rec.parsed_tags(),
            "relationship": (self._favor.relationship_label(rec.relationship)
                             if rec.relationship else ""),
            "is_master": self._favor.is_master(user_id),
        }
