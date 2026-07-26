"""心弦好感度 - 存储后端抽象接口。

上层（services）只依赖本接口；实现类负责并发安全与事务性。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.models import FavorRecord


class StorageBackend(ABC):
    """好感度存储后端接口。"""

    @abstractmethod
    async def init(self) -> None:
        """初始化存储（建库、迁移）。"""

    @abstractmethod
    async def get(self, group_id: str, user_id: str) -> FavorRecord | None:
        """读取一条记录，不存在返回 None。"""

    @abstractmethod
    async def apply_delta(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        max_favor: float,
        min_favor: float = -100.0,
        decay: tuple[float, float, float] | None = None,
    ) -> tuple[FavorRecord, float]:
        """原子地增减好感度（锁内读-改-写），封顶 min_favor..max_favor。

        decay: 非空时为 (per_day, grace_days, baseline)，落库前先把存量衰减到当下
            （锁定时间衰减）；为 None 时不衰减。

        Returns:
            (更新后的记录, 实际生效的变化量)。越界截断时实际变化量小于 delta。
            delta 与返回值精度均为一位小数。
        """

    @abstractmethod
    async def set_value(self, group_id: str, user_id: str, value: float) -> FavorRecord:
        """直接设定好感度数值（精度一位小数）。"""

    @abstractmethod
    async def set_relationship(self, group_id: str, user_id: str, relationship: str) -> None:
        """设定关系类型标签（不影响好感度数值）。空串表示清除。"""

    @abstractmethod
    async def set_nickname(self, group_id: str, user_id: str, nickname: str) -> None:
        """更新成员昵称（不改好感数值）；发言时捕获，供排行图/WebUI 显示名字。"""

    @abstractmethod
    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        """群内好感度排行（降序）。"""

    @abstractmethod
    async def list_favor(self, group_id: str | None = None, limit: int = 500) -> list[FavorRecord]:
        """列出当前好感度记录（含 relationship）；group_id 为空则全部群，按 updated_at 倒序。"""

    @abstractmethod
    async def distinct_groups(self) -> list[dict]:
        """有当前好感记录的群列表（含每群人数），供面板群筛选。"""

    @abstractmethod
    async def daily_gain(self, group_id: str, user_id: str, day: str) -> float:
        """当日净增量（带符号，精度一位小数）。day 格式 YYYY-MM-DD。"""

    @abstractmethod
    async def add_daily_gain(
        self, group_id: str, user_id: str, day: str, delta: float
    ) -> None:
        """累加当日净增量。"""

    @abstractmethod
    async def last_event_at(self, group_id: str, user_id: str, key: str) -> float | None:
        """某事件最近触发时间（epoch 秒），无记录返回 None。"""

    @abstractmethod
    async def touch_event(self, group_id: str, user_id: str, key: str, ts: float) -> None:
        """记录事件触发时间。"""

    @abstractmethod
    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        """重置数据。user_id 为 None 时清空整群。"""

    @abstractmethod
    async def add_log(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        favor_before: float,
        favor_after: float,
        reason: str,
        source: str,
        ts: float,
        message: str = "",
    ) -> None:
        """追加一条好感度变动流水（供 WebUI 展示增减大小与原因）。

        message: 触发该次变动的用户发言原文（仅 judge 路径记，其它路径留空）。
        """

    @abstractmethod
    async def query_logs(
        self,
        group_id: str | None = None,
        user_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """查询变动流水（时间倒序）。每条 dict 含 id/group_id/user_id/delta/favor_before/favor_after/reason/source/ts/message/reversed。"""

    @abstractmethod
    async def get_log(self, log_id: int) -> dict | None:
        """按 id 取单条变动流水（撤销时读 delta/group/user/reversed）。不存在返回 None。"""

    @abstractmethod
    async def mark_reversed(self, log_id: int) -> None:
        """把某条流水标记为已撤销（reversed=1），防重复撤销。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭并释放资源。"""
