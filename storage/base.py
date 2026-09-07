"""心弦好感度 - 存储后端抽象接口。

上层（services）只依赖本接口；实现类负责并发安全与事务性。

**原子性契约（承重假设）**：FavorService 的「每日限幅读 → apply_delta 写 →
当日额度记账」三步，以及 set_favor / apply_undo（单事务撤销）的内部序列，
均依赖实现类的这些方法体内不含真实挂起点（同步代码的 async 方法在事件
循环里原子执行）。**只读路径（ranking/list_favor/distinct_groups/
query_logs）例外（X9）**：经 asyncio.to_thread 执行、会真实让出——它们
不参与上述读-改-写序列，线程与事件循环经实现类同一把 threading.Lock
互斥。若引入真异步后端（aiosqlite/Redis 等），必须把全部读-改-写序列
收进同一把锁/事务内，否则每日限幅与撤销都会被并发绕过。
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
        decay: tuple[float, float, float, float] | None = None,
        default_favor: float = 0.0,
    ) -> tuple[FavorRecord, float]:
        """原子地增减好感度（锁内读-改-写），封顶 min_favor..max_favor。

        decay: 非空时为 (half_life_base, growth, h_max, baseline)。落库前先把
            存量按指数遗忘曲线衰减到当下（锁定时间衰减）；delta>0 时半衰期
            按巩固规则增长并回写。为 None 时不衰减不巩固。
        default_favor: 无记录时的起始基数（与新成员初始好感配置一致）。
        Returns:
            (更新后的记录, 实际生效的变化量)。越界截断时实际变化量小于 delta。
            delta 与返回值精度均为一位小数。
        """

    @abstractmethod
    async def set_value(self, group_id: str, user_id: str, value: float) -> FavorRecord:
        """直接设定好感度数值（精度一位小数）。"""

    @abstractmethod
    async def set_relationship(
        self, group_id: str, user_id: str, relationship: str
    ) -> None:
        """设定关系类型标签（不影响好感度数值）。空串表示清除。"""

    @abstractmethod
    async def set_nickname(self, group_id: str, user_id: str, nickname: str) -> None:
        """更新成员昵称（不改好感数值）；发言时捕获，供排行图/WebUI 显示名字。"""

    @abstractmethod
    async def set_points(self, group_id: str, user_id: str, points: list[dict]) -> None:
        """写入印象点集（P-C 带权点模型；不改好感数值）。"""

    @abstractmethod
    async def set_profile(
        self,
        group_id: str,
        user_id: str,
        impression: str,
        tags: list[str],
        points: list[dict],
    ) -> None:
        """一次写入印象+标签+点集（P-C 原子档案更新；不改好感数值）。"""

    @abstractmethod
    async def set_impression(
        self, group_id: str, user_id: str, impression: str, tags: list[str]
    ) -> None:
        """写入成员印象与标签（不改好感数值）。tags 为展示顺序已定的标签列表。"""

    @abstractmethod
    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        """群内好感度排行（降序）。"""

    @abstractmethod
    async def list_favor(
        self, group_id: str | None = None, limit: int = 500
    ) -> list[FavorRecord]:
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
    async def last_event_at(
        self, group_id: str, user_id: str, key: str
    ) -> float | None:
        """某事件最近触发时间（epoch 秒），无记录返回 None。"""

    @abstractmethod
    async def touch_event(
        self, group_id: str, user_id: str, key: str, ts: float
    ) -> None:
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

        message: 触发该次变动的用户发言摘录（仅 judge 路径记，其它路径留空）。
        数据最小化契约：reason 与 message 落库前统一截断到 200 字符
        （两者都可能携带聊天内容，存储层兜底所有调用路径）。
        """

    @abstractmethod
    async def query_logs(
        self,
        group_id: str | None = None,
        user_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
        fuzzy: bool = False,
    ) -> list[dict]:
        """查询变动流水（时间倒序）。每条 dict 含 id/group_id/user_id/delta/favor_before/favor_after/reason/source/ts/message/reversed。

        user_id 默认**精确匹配**（内部路径——同日衰减/修复期/近期印象/印象
        汇总/里程碑——语义均要求精确；QQ 互为子串时模糊匹配会把他人流水
        算进本人记忆/修复期）。fuzzy=True 仅供 WebUI 搜索框（子串匹配）。
        """

    @abstractmethod
    async def get_log(self, log_id: int) -> dict | None:
        """按 id 取单条变动流水（撤销时读 delta/group/user/reversed）。不存在返回 None。"""

    @abstractmethod
    async def mark_reversed(self, log_id: int) -> None:
        """把某条流水标记为已撤销（reversed=1），防重复撤销。"""

    @abstractmethod
    async def apply_undo(
        self,
        log_id: int,
        *,
        max_favor: float,
        min_favor: float,
        effective=None,
        default_favor: float = 0.0,
    ) -> dict:
        """单事务撤销一条流水（X4）。

        校验未撤销 → 反向落地（含 undo 流水与 updated_at 刷新）→ 标记原行
        reversed，全部步骤在同一事务内提交——拆开的检查-执行序列（get →
        set_favor → mark_reversed）存在 TOCTOU：并发双击会双重反向扣分，
        中途失败会留下"已扣分但未标记"的可重复撤销态。

        effective: 可选的纯同步衰减读值函数 ``(stored, updated_at,
        half_life) -> float``（FavorService._effective 的无 await 投影，
        撤销以衰减后的有效值为基数——与旧实现语义一致）。必须是纯函数
        （存储原子性契约同款约束）。None=直接用存量值。
        default_favor: 无 favor 行时的基数（与 apply_delta 语义一致）。
        实际变化量为 0 时（钳到值域边界）不追加 undo 流水，但仍标记
        reversed——对齐旧 set_favor 路径行为。
        Returns:
            {"group_id", "user_id", "before", "after", "delta"}。
        Raises:
            ValueError: 记录不存在 / 该变动已撤销。
        """

    @abstractmethod
    async def close(self) -> None:
        """关闭并释放资源。"""
