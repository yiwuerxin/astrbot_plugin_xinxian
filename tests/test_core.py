"""心弦好感度 - 核心层单元测试。

只测不依赖 AstrBot 的部分：等级映射、规则匹配、身份判定、
好感度增减（封顶/冷却/每日限幅）。运行：pytest tests/ -v
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_xinxian.core.events import EventType, RuleMatcher  # noqa: E402
from astrbot_plugin_xinxian.core.identity import is_master, parse_master_ids  # noqa: E402
from astrbot_plugin_xinxian.core.levels import LevelTable  # noqa: E402
from astrbot_plugin_xinxian.core.models import FavorRecord  # noqa: E402
from astrbot_plugin_xinxian.services.favor_service import FavorService  # noqa: E402
from astrbot_plugin_xinxian.storage.sqlite_backend import SQLiteBackend  # noqa: E402


# ---------------- 等级表 ----------------

class TestLevelTable:
    def setup_method(self):
        self.table = LevelTable.from_config(None)

    def test_boundaries(self):
        assert self.table.level_of(0).name == "陌生"
        assert self.table.level_of(9).name == "陌生"
        assert self.table.level_of(10).name == "认识"
        assert self.table.level_of(29).name == "认识"
        assert self.table.level_of(30).name == "友好"
        assert self.table.level_of(54).name == "友好"
        assert self.table.level_of(55).name == "亲密"
        assert self.table.level_of(79).name == "亲密"
        assert self.table.level_of(80).name == "挚友"
        assert self.table.level_of(94).name == "挚友"
        assert self.table.level_of(95).name == "挚爱"
        assert self.table.level_of(100).name == "挚爱"

    def test_custom_threshold(self):
        table = LevelTable.from_config({"levels": {"youhao": {"min": 50}}})
        assert table.level_of(49).name == "认识"
        assert table.level_of(50).name == "友好"


# ---------------- 规则匹配 ----------------

class TestRuleMatcher:
    def setup_method(self):
        self.matcher = RuleMatcher.from_config(None)

    def test_praised_keyword(self):
        hits = self.matcher.match("小千好可爱")
        assert [r.event for r in hits] == [EventType.PRAISED]

    def test_insulted_keyword(self):
        hits = self.matcher.match("你就是个沙比")
        assert [r.event for r in hits] == [EventType.INSULTED]

    def test_at_and_reply(self):
        hits = self.matcher.match("在吗", has_at_bot=True, is_reply_bot=True)
        events = {r.event for r in hits}
        assert EventType.AT_MENTION in events
        assert EventType.REPLY_BOT in events

    def test_first_today(self):
        hits = self.matcher.match("早", is_first_today=True)
        assert any(r.event == EventType.DAILY_FIRST for r in hits)
        hits2 = self.matcher.match("早", is_first_today=False)
        assert not any(r.event == EventType.DAILY_FIRST for r in hits2)

    def test_no_hit(self):
        assert self.matcher.match("今天天气不错") == []


# ---------------- 身份 ----------------

class TestIdentity:
    def test_parse(self):
        assert parse_master_ids("1109841333, 222，333") == ["1109841333", "222", "333"]
        assert parse_master_ids("") == []

    def test_is_master(self):
        assert is_master("1109841333", ["1109841333"])
        assert not is_master("999", ["1109841333"])


# ---------------- 好感度增减（内存级 SQLite） ----------------

def _make_service(tmp_path, **kw) -> FavorService:
    storage = SQLiteBackend(tmp_path / "test.db")
    asyncio.run(storage.init())
    defaults = dict(max_favor=100, default_favor=0, daily_cap_up=15, daily_cap_down=15)
    defaults.update(kw)
    return FavorService(storage, LevelTable.from_config(None), **defaults)


class TestFavorService:
    def test_apply_and_clamp_max(self, tmp_path):
        svc = _make_service(tmp_path, daily_cap_up=200)
        ch = asyncio.run(svc.change("g1", "u1", 150))
        assert ch.favor_after == 100  # 封顶
        lv = svc.level_of(ch.favor_after)
        assert lv.name == "挚爱"

    def test_floor_zero(self, tmp_path):
        svc = _make_service(tmp_path, daily_cap_down=200)
        asyncio.run(svc.change("g1", "u1", 50))
        ch = asyncio.run(svc.change("g1", "u1", -80))
        assert ch.favor_after == 0

    def test_daily_cap_up(self, tmp_path):
        svc = _make_service(tmp_path, daily_cap_up=10)
        ch1 = asyncio.run(svc.change("g1", "u1", 8))
        assert ch1.delta == 8
        ch2 = asyncio.run(svc.change("g1", "u1", 8))
        assert ch2.delta == 2  # 当日只剩 2 分额度
        assert ch2.clamped
        ch3 = asyncio.run(svc.change("g1", "u1", 8))
        assert ch3.delta == 0  # 额度耗尽

    def test_daily_cap_down(self, tmp_path):
        svc = _make_service(tmp_path, daily_cap_up=200, daily_cap_down=10)
        # set_favor 不占每日额度，先把底子垫到 50
        asyncio.run(svc.set_favor("g1", "u1", 50))
        ch1 = asyncio.run(svc.change("g1", "u1", -8))
        assert ch1.delta == -8
        ch2 = asyncio.run(svc.change("g1", "u1", -8))
        assert ch2.delta == -2  # 当日净降额度只剩 2
        assert ch2.clamped
        ch3 = asyncio.run(svc.change("g1", "u1", -8))
        assert ch3.delta == 0

    def test_rules_cooldown(self, tmp_path):
        svc = _make_service(tmp_path)
        matcher = RuleMatcher.from_config(None)
        rules = matcher.match("小千好可爱")
        ch1 = asyncio.run(svc.apply_rules("g1", "u1", rules))
        assert ch1.delta == 3
        # 冷却期内同类规则不再生效
        ch2 = asyncio.run(svc.apply_rules("g1", "u1", rules))
        assert ch2.delta == 0
        assert ch2.clamped

    def test_per_group_independent(self, tmp_path):
        svc = _make_service(tmp_path, daily_cap_up=200)
        asyncio.run(svc.change("g1", "u1", 30))
        rec_a = asyncio.run(svc.get("g1", "u1"))
        rec_b = asyncio.run(svc.get("g2", "u1"))
        assert rec_a.favor == 30
        assert rec_b.favor == 0  # 每群独立

    def test_set_and_ranking(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", 10))
        asyncio.run(svc.set_favor("g1", "u2", 90))
        asyncio.run(svc.set_favor("g1", "u3", 50))
        rows = asyncio.run(svc.ranking("g1", 10))
        assert [r.user_id for r in rows] == ["u2", "u3", "u1"]

    def test_reset(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", 60))
        asyncio.run(svc.reset("g1", "u1"))
        rec = asyncio.run(svc.get("g1", "u1"))
        assert rec.favor == 0

    def test_first_today(self, tmp_path):
        svc = _make_service(tmp_path)
        assert asyncio.run(svc.is_first_today("g1", "u1")) is True
        matcher = RuleMatcher.from_config(None)
        rules = matcher.match("早", is_first_today=True)
        asyncio.run(svc.apply_rules("g1", "u1", rules))
        assert asyncio.run(svc.is_first_today("g1", "u1")) is False
