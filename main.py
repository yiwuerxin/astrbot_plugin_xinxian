"""心弦好感度 - 插件入口（组合根）。

装配顺序：storage → levels → services → deps → api。
所有 @filter 钩子均为本 Star 类的薄壳方法（AstrBot 只扫描 Star 实例方法），
实际逻辑全部转发给 api/ 层的纯 handler 函数。
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

try:
    from astrbot.api import AstrBotConfig
except ImportError:  # pragma: no cover
    from astrbot.api.all import AstrBotConfig

from .api import commands as cmd
from .api import llm_tools as tools
from .api.facade import XinxianFacade
from .api.listeners import Deps, on_group_message, on_llm_request
from .core.events import RuleMatcher
from .core.identity import parse_master_ids
from .core.levels import LevelTable
from .core.relationship import RelationshipTable
from .services.favor_service import FavorService
from .services.inject_service import InjectService
from .services.judge_service import JudgeService
from .storage.sqlite_backend import SQLiteBackend

_PLUGIN_DIR = Path(__file__).resolve().parent


def _read_resource(rel: str) -> str:
    return (_PLUGIN_DIR / rel).read_text(encoding="utf-8").strip()


@register(
    "astrbot_plugin_xinxian",
    "yiwuerxin",
    "小千的心弦好感度系统",
    "1.9.0",
    "https://github.com/yiwuerxin/astrbot_plugin_xinxian",
)
class XinxianPlugin(Star):
    """心弦好感度插件。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        try:
            data_dir = StarTools.get_data_dir()
        except TypeError:  # 兼容需要显式插件名的版本
            data_dir = StarTools.get_data_dir("astrbot_plugin_xinxian")

        max_favor = float(config.get("max_favor", 100))
        min_favor = float(config.get("min_favor", -100))
        levels = LevelTable.from_config(config)
        master_ids = parse_master_ids(config.get("master_ids", ""))

        self._storage = SQLiteBackend(data_dir / "xinxian.db")
        decay_cfg = config.get("decay") or {}
        rel_cfg = config.get("relationship") or {}
        relationships = (
            RelationshipTable.from_config(rel_cfg)
            if bool(rel_cfg.get("enabled", False))
            else None
        )
        self._relationships = relationships
        self._favor = FavorService(
            self._storage,
            levels,
            max_favor=max_favor,
            min_favor=min_favor,
            default_favor=float(config.get("default_favor", 0)),
            daily_cap_up=float(config.get("daily_cap_up", 15)),
            daily_cap_down=float(config.get("daily_cap_down", 15)),
            master_ids=master_ids,
            decay_enabled=bool(decay_cfg.get("enabled", False)),
            decay_per_day=float(decay_cfg.get("per_day", 1.0)),
            decay_grace_days=float(decay_cfg.get("grace_days", 3)),
            decay_baseline=float(decay_cfg.get("baseline", 0.0)),
            relationships=relationships,
        )

        judge_cfg = config.get("judge") or {}
        inject_cfg = config.get("inject") or {}

        template = (inject_cfg.get("template") or "").strip() or _read_resource(
            "resources/prompts/inject_template.txt"
        )
        self._inject = InjectService(
            levels,
            template,
            master_title=inject_cfg.get("master_title", "主人"),
            max_favor=max_favor,
            relationships=relationships,
        )
        self._judge = JudgeService(
            context,
            self._storage,
            enabled=bool(judge_cfg.get("enabled", True)),
            provider_id=(judge_cfg.get("provider_id") or "").strip() or None,
            cooldown_sec=int(judge_cfg.get("cooldown_sec", 120)),
            max_abs_delta=float(judge_cfg.get("max_abs_delta", 3)),
            only_when_at_or_reply=bool(judge_cfg.get("only_when_at_or_reply", True)),
            prompt_template=_read_resource(
                "resources/prompts/judge_prompt_narrative.txt"
                if bool(judge_cfg.get("narrative_reason", False))
                else "resources/prompts/judge_prompt.txt"
            ),
            force_session_model=bool(judge_cfg.get("narrative_reason", False)),
            context_window=int(judge_cfg.get("context_window", 0)),
        )
        self._deps = Deps(
            favor=self._favor,
            judge=self._judge,
            inject=self._inject,
            matcher=RuleMatcher.from_config(config),
            inject_enabled=bool(inject_cfg.get("enabled", True)),
            memory_count=int(inject_cfg.get("memory_count", 3)),
            memory_days=int(inject_cfg.get("memory_days", 7)),
        )
        self._ranking_limit = int((config.get("command") or {}).get("ranking_limit", 10))

        # 跨插件 API：context.get_registered_star("astrbot_plugin_xinxian").star_cls.api
        self.api = XinxianFacade(self._favor)

        # 原生 dashboard 页面 API（框架支持时注册，内嵌于主面板，无独立端口/鉴权）
        from .api.page_api import PageApi
        self._page_api = PageApi(self._favor, self._storage)
        self._page_api.register(context)

    async def initialize(self) -> None:
        await self._storage.init()
        logger.info("[心弦] 好感度插件已加载")

    async def terminate(self) -> None:
        await self._storage.close()
        logger.info("[心弦] 好感度插件已卸载")

    # ---------------- 事件钩子（薄壳转发） ----------------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def _on_group_msg(self, event: AstrMessageEvent):
        await on_group_message(self._deps, event)

    @filter.on_llm_request()
    async def _on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        await on_llm_request(self._deps, event, req)

    # ---------------- 指令 ----------------

    @filter.command("好感度")
    async def _cmd_query(self, event: AstrMessageEvent):
        """查询自己对小千的好感度"""
        yield event.plain_result(await cmd.handle_query(self._favor, event))

    @filter.command("好感排行")
    async def _cmd_rank(self, event: AstrMessageEvent):
        """查看本群对小千的好感度排行"""
        yield event.plain_result(
            await cmd.handle_ranking(self._favor, event, self._ranking_limit)
        )

    @filter.command("好感设置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_set(self, event: AstrMessageEvent, target: str = "", value: float = 0.0):
        """设置某成员好感度（管理员，支持一位小数与负值）。用法：/好感设置 QQ号 数值"""
        yield event.plain_result(await cmd.handle_set(self._favor, event, target, value))

    @filter.command("好感重置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_reset(self, event: AstrMessageEvent, target: str = ""):
        """重置好感度（管理员）。用法：/好感重置 [QQ号]，不带参数清空整群"""
        yield event.plain_result(await cmd.handle_reset(self._favor, event, target))

    @filter.command("关系设置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_set_rel(self, event: AstrMessageEvent, target: str = "", key: str = ""):
        """设置某成员与小千的关系（管理员）。用法：/关系设置 QQ号 类型"""
        yield event.plain_result(
            await cmd.handle_set_relationship(self._favor, self._relationships, event, target, key)
        )

    # ---------------- LLM 工具 ----------------

    @filter.llm_tool(name="query_favor")
    async def _tool_query(self, event: AstrMessageEvent, target: str = "") -> str:
        """查询某群成员对小千的好感度与当前等级。

        Args:
            target(str): 目标成员的 QQ 号，留空则查询发言人自己
        """
        return await tools.tool_query_favor(self._favor, event, target)

    @filter.llm_tool(name="query_favor_ranking")
    async def _tool_rank(self, event: AstrMessageEvent) -> str:
        """查询本群成员对小千的好感度排行榜"""
        return await tools.tool_query_ranking(self._favor, event, self._ranking_limit)
