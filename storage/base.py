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
    ) -> tuple[FavorRecord, float]:
        """原子地增减好感度（锁内读-改-写），封顶 min_favor..max_favor。

        Returns:
            (更新后的记录, 实际生效的变化量)。越界截断时实际变化量小于 delta。
            delta 与返回值精度均为一位小数。
        """

    @abstractmethod
    async def set_value(self, group_id: str, user_id: str, value: float) -> FavorRecord:
        """直接设定好感度数值（精度一位小数）。"""

    @abstractmethod
    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        """群内好感度排行（降序）。"""

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
    async def close(self) -> None:
        """关闭并释放资源。"""
