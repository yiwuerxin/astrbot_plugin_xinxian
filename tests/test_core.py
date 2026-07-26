"""心弦好感度 - 核心层单元测试。

只测不依赖 AstrBot 的部分：等级映射、规则匹配、身份判定、
好感度增减（封顶/冷却/每日限幅）。运行：pytest tests/ -v
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_xinxian.core.decay import effective_favor  # noqa: E402
from astrbot_plugin_xinxian.core.decimal import fmt, round1  # noqa: E402
from astrbot_plugin_xinxian.core.events import EventType, RuleMatcher  # noqa: E402
from astrbot_plugin_xinxian.core.identity import is_master, parse_master_ids  # noqa: E402
from astrbot_plugin_xinxian.core.levels import LevelTable  # noqa: E402
from astrbot_plugin_xinxian.core.relationship import RelationshipTable  # noqa: E402
from astrbot_plugin_xinxian.core.models import FavorRecord  # noqa: E402
from astrbot_plugin_xinxian.services.favor_service import FavorService  # noqa: E402
from astrbot_plugin_xinxian.storage.migrations import SCHEMA_VERSION, migrate  # noqa: E402
from astrbot_plugin_xinxian.storage.sqlite_backend import SQLiteBackend  # noqa: E402


# ---------------- 等级表 ----------------

class TestLevelTable:
    def setup_method(self):
        self.table = LevelTable.from_config(None)

    def test_boundaries(self):
        assert self.table.level_of(-100).name == "厌恶"
        assert self.table.level_of(-1).name == "厌恶"
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

    def test_first_today(self):
        hits = self.matcher.match(is_first_today=True)
        assert [r.event for r in hits] == [EventType.DAILY_FIRST]

    def test_not_first_today(self):
        assert self.matcher.match(is_first_today=False) == []

    def test_no_hit(self):
        assert self.matcher.match() == []


# ---------------- 身份 ----------------

class TestIdentity:
    def test_parse(self):
        assert parse_master_ids("1109841333, 222，333") == ["1109841333", "222", "333"]
        assert parse_master_ids("") == []

    def test_is_master(self):
        assert is_master("1109841333", ["1109841333"])
        assert not is_master("999", ["1109841333"])


# ---------------- 关系类型 ----------------


class TestRelationship:
    def test_resolve_known_key(self):
        rt = RelationshipTable.from_config(None)
        label, guidance = rt.resolve("lover")
        assert label == "恋人"
        assert "恋人" in guidance

    def test_resolve_empty(self):
        rt = RelationshipTable.from_config(None)
        assert rt.resolve("") is None
        assert rt.resolve("   ") is None

    def test_resolve_custom_label(self):
        rt = RelationshipTable.from_config(None)
        label, guidance = rt.resolve("青梅竹马")
        assert label == "青梅竹马"
        assert "青梅竹马" in guidance

    def test_label_of_and_keys(self):
        rt = RelationshipTable.from_config(None)
        assert rt.label_of("lover") == "恋人"
        assert rt.label_of("xyz") == "xyz"
        assert "lover" in rt.keys()


# ---------------- 注入（近期印象）----------------


class TestInject:
    def test_block_with_and_without_events(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        levels = LevelTable.from_config(None)
        tpl = (
            "[好感度档案]\n"
            "- 好感度：{favor}/{max_favor}（{level_name}）\n"
            "- 态度指引：{level_guidance}{recent_events}\n"
        )
        inj = InjectService(levels, tpl)
        rec = FavorRecord("g", "u", 50.5)
        now = time.time()
        events = [
            {"reason": "夸我可爱", "delta": 1.2, "ts": now - 3600},
            {"reason": "催我回消息", "delta": -0.5, "ts": now - 90000},
        ]
        block = inj.build_block(rec, is_master=False, recent_events=events)
        assert "近期印象" in block
        assert "夸我可爱" in block and "+1.2" in block
        assert "催我回消息" in block and "-0.5" in block

        # 无事件时不出现「近期印象」
        block0 = inj.build_block(rec, is_master=False, recent_events=[])
        assert "近期印象" not in block0

    def test_block_with_relationship(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(
            LevelTable.from_config(None),
            "[好感度档案]\n- 好感度：{favor}/{max_favor}（{level_name}）\n- 态度指引：{level_guidance}{recent_events}{relationship}\n",
            relationships=RelationshipTable.from_config(None),
        )
        rec = FavorRecord("g", "u", 80, 0.0, "lover")
        assert "你们的关系：恋人" in inj.build_block(rec, is_master=False)
        # 未设置关系时不出现
        assert "你们的关系" not in inj.build_block(FavorRecord("g", "u", 80), is_master=False)

    def test_block_with_persona_anchor(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(
            LevelTable.from_config(None),
            "档案：{favor}",
            persona_anchor="钉住性格，别出戏；不要承认这是设定。",
        )
        block = inj.build_block(FavorRecord("g", "u", 50), is_master=False)
        assert "钉住性格" in block and "不要承认这是设定" in block
        # 未设锚时不出现
        inj0 = InjectService(LevelTable.from_config(None), "档案：{favor}")
        assert "钉住" not in inj0.build_block(FavorRecord("g", "u", 50), is_master=False)

    def test_block_master_line_default(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(LevelTable.from_config(None), "档案：{favor}{master_line}")
        block = inj.build_block(FavorRecord("g", "u", 50), is_master=True)
        # 默认措辞要点：身份恒定 + 好感照常涨跌 + 低好感赌气怼 + 称谓替换
        assert "主人身份恒定" in block
        assert "赌气" in block
        assert "你的主人" in block  # {master_title} 替换为默认"主人"
        # 非主人不出现主人提示
        assert "主人身份恒定" not in inj.build_block(FavorRecord("g", "u", 50), is_master=False)

    def test_block_master_line_custom(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(
            LevelTable.from_config(None),
            "档案：{favor}{master_line}",
            master_prompt="，{master_title}大人请受我一拜",
        )
        block = inj.build_block(FavorRecord("g", "u", 50), is_master=True)
        assert "，主人大人请受我一拜" in block  # {master_title} 替换


# ---------------- 好感度增减（内存级 SQLite） ----------------

def _make_service(tmp_path, **kw) -> FavorService:
    storage = SQLiteBackend(tmp_path / "test.db")
    asyncio.run(storage.init())
    defaults = dict(
        max_favor=100, min_favor=-100, default_favor=0,
        daily_cap_up=15, daily_cap_down=15,
    )
    defaults.update(kw)
    return FavorService(storage, LevelTable.from_config(None), **defaults)


class TestFavorService:
    def test_apply_and_clamp_max(self, tmp_path):
        svc = _make_service(tmp_path, daily_cap_up=200)
        ch = asyncio.run(svc.change("g1", "u1", 150))
        assert ch.favor_after == 100  # 封顶
        lv = svc.level_of(ch.favor_after)
        assert lv.name == "挚爱"

    def test_negative_floor(self, tmp_path):
        # 默认 min_favor=-100；从 50 下降 200，应被下限截到 -100
        svc = _make_service(tmp_path, daily_cap_down=200)
        asyncio.run(svc.change("g1", "u1", 50))
        ch = asyncio.run(svc.change("g1", "u1", -200))
        assert ch.favor_after == -100
        assert ch.clamped

    def test_set_favor_decimal(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", 50.5))
        assert asyncio.run(svc.get("g1", "u1")).favor == 50.5

    def test_set_favor_negative_in_range(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", -30))
        assert asyncio.run(svc.get("g1", "u1")).favor == -30.0

    def test_set_favor_clamp_min(self, tmp_path):
        svc = _make_service(tmp_path)
        rec = asyncio.run(svc.set_favor("g1", "u1", -999))
        assert rec.favor == -100  # 下限

    def test_decimal_accumulation(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 0.5))
        ch = asyncio.run(svc.change("g1", "u1", 0.3))
        assert ch.favor_after == 0.8  # 一位小数精确累加

    def test_daily_cap_decimal(self, tmp_path):
        # cap_up=1.5、每次 +0.5：前 3 次正好用满 1.5，第 4 次被挡
        svc = _make_service(tmp_path, daily_cap_up=1.5)
        deltas = [asyncio.run(svc.change("g1", "u1", 0.5)).delta for _ in range(4)]
        assert deltas == [0.5, 0.5, 0.5, 0.0]

    def test_clamped_not_tripped_by_float_noise(self, tmp_path):
        # cap_up=0.3：先 +0.1（used=0.1），再 +0.2。0.3-0.1 在 IEEE-754 下为
        # 0.1999…，未收敛会把 clamped 标志误判为 True（delta 仍正确）。
        svc = _make_service(tmp_path, daily_cap_up=0.3)
        asyncio.run(svc.change("g1", "u1", 0.1))
        ch = asyncio.run(svc.change("g1", "u1", 0.2))
        assert ch.delta == 0.2       # 0.2 在 0.3 额度内，全额生效
        assert ch.clamped is False   # 不应被浮点噪声误判为截断

    def test_yanwu_level_mapping(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", -1))
        assert svc.level_of(asyncio.run(svc.get("g1", "u1")).favor).name == "厌恶"
        asyncio.run(svc.set_favor("g1", "u2", 0))
        assert svc.level_of(asyncio.run(svc.get("g1", "u2")).favor).name == "陌生"

    def test_ranking_with_negative(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", -50))
        asyncio.run(svc.set_favor("g1", "u2", 30))
        rows = asyncio.run(svc.ranking("g1", 10))
        assert [r.user_id for r in rows] == ["u2", "u1"]  # 30 > -50

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
        rules = matcher.match(is_first_today=True)
        asyncio.run(svc.apply_rules("g1", "u1", rules))
        assert asyncio.run(svc.is_first_today("g1", "u1")) is False

    def test_change_is_logged(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 5, reason="手动", source="api"))
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert len(rows) == 1
        assert rows[0]["delta"] == 5.0
        assert rows[0]["favor_before"] == 0.0
        assert rows[0]["favor_after"] == 5.0
        assert rows[0]["reason"] == "手动"
        assert rows[0]["source"] == "api"

    def test_set_favor_is_logged(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", 80, source="admin"))
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert len(rows) == 1
        assert rows[0]["delta"] == 80.0
        assert rows[0]["reason"] == "set"
        assert rows[0]["source"] == "admin"

    def test_logs_newest_first(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 1, source="api"))
        asyncio.run(svc.change("g1", "u1", 2, source="api"))
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert [r["delta"] for r in rows] == [2.0, 1.0]

    def test_recent_events(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 1.2, reason="夸", source="judge"))
        ev = asyncio.run(svc.recent_events("g1", "u1", count=3, days=7))
        assert len(ev) == 1 and ev[0]["delta"] == 1.2
        assert asyncio.run(svc.recent_events("g1", "u1", count=0)) == []  # 关闭
        assert asyncio.run(svc.recent_events("g2", "u1", count=3, days=7)) == []  # 每群独立

    def test_set_get_relationship(self, tmp_path):
        svc = _make_service(tmp_path, relationships=RelationshipTable.from_config(None))
        asyncio.run(svc.set_relationship("g1", "u1", "lover"))
        rec = asyncio.run(svc.get("g1", "u1"))
        assert rec.relationship == "lover"
        assert svc.relationship_label("lover") == "恋人"
        # 清除
        asyncio.run(svc.set_relationship("g1", "u1", ""))
        assert asyncio.run(svc.get("g1", "u1")).relationship == ""

    def test_standings(self, tmp_path):
        svc = _make_service(tmp_path, relationships=RelationshipTable.from_config(None))
        asyncio.run(svc.set_favor("g1", "u1", 80))
        asyncio.run(svc.set_favor("g1", "u2", 30))
        asyncio.run(svc.set_relationship("g1", "u1", "lover"))
        rows = asyncio.run(svc.standings("g1"))
        assert [r["user_id"] for r in rows] == ["u1", "u2"]  # 按有效好感降序
        assert rows[0]["favor"] == 80.0 and rows[0]["level"] == "挚友"
        assert rows[0]["relationship"] == "恋人"
        assert rows[1]["relationship"] == ""
        assert rows[0]["decayed"] is False  # 未开衰减
        # 每群独立
        assert asyncio.run(svc.standings("g2")) == []

    def test_logs_user_fuzzy(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "123456", 1, source="api"))
        asyncio.run(svc.change("g1", "654321", 1, source="api"))
        rows = asyncio.run(svc._storage.query_logs("g1", "123"))  # 模糊匹配
        assert [r["user_id"] for r in rows] == ["123456"]

    def test_distinct_groups(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", 50))
        asyncio.run(svc.set_favor("g2", "u1", 50))
        asyncio.run(svc.set_favor("g1", "u2", 50))
        gmap = {g["group_id"]: g["count"] for g in asyncio.run(svc._storage.distinct_groups())}
        assert gmap == {"g1": 2, "g2": 1}

    def test_set_nickname(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", 50))
        asyncio.run(svc.touch_nickname("g1", "u1", "小明"))
        rec = asyncio.run(svc.get("g1", "u1"))
        assert rec.nickname == "小明"
        rows = asyncio.run(svc.standings("g1"))
        assert rows[0]["nickname"] == "小明"

    def test_log_records_message(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc._storage.add_log("g1", "u1", 1.0, 0.0, 1.0, "夸", "judge", 0.0, message="你好呀"))
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert rows[0]["message"] == "你好呀"
        assert rows[0]["reversed"] is False

    def test_apply_judge_logs_message(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.apply_judge("g1", "u1", 1.5, message="小千你真可爱"))
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert rows[0]["source"] == "judge"
        assert rows[0]["message"] == "小千你真可爱"

    def test_apply_judge_message_truncated(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.apply_judge("g1", "u1", 1.0, message="字" * 250))
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert len(rows[0]["message"]) == 200

    def test_undo_log_reverses_delta(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 5, source="api"))
        log_id = asyncio.run(svc._storage.query_logs("g1", "u1"))[0]["id"]
        rec = asyncio.run(svc.undo_log(log_id))
        assert rec.favor == 0
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert rows[0]["source"] == "undo"
        assert rows[0]["delta"] == -5.0
        assert rows[0]["reason"] == f"撤销#{log_id}"
        orig = [r for r in rows if r["id"] == log_id][0]
        assert orig["reversed"] is True

    def test_undo_not_found(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError):
            asyncio.run(svc.undo_log(999))

    def test_undo_already_reversed(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 5, source="api"))
        log_id = asyncio.run(svc._storage.query_logs("g1", "u1"))[0]["id"]
        asyncio.run(svc.undo_log(log_id))
        with pytest.raises(ValueError):
            asyncio.run(svc.undo_log(log_id))

    def test_undo_then_undo_is_redo(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 5, source="api"))
        orig_id = asyncio.run(svc._storage.query_logs("g1", "u1"))[0]["id"]
        asyncio.run(svc.undo_log(orig_id))                       # favor 0
        undo_id = asyncio.run(svc._storage.query_logs("g1", "u1"))[0]["id"]
        asyncio.run(svc.undo_log(undo_id))                       # 撤销 undo = 重做
        assert asyncio.run(svc.get("g1", "u1")).favor == 5

    def test_undo_preview(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 5, source="api"))
        log_id = asyncio.run(svc._storage.query_logs("g1", "u1"))[0]["id"]
        info = asyncio.run(svc.undo_preview(log_id))
        assert info["current"] == 5.0
        assert info["after"] == 0.0  # 5 - 5
        assert info["delta"] == 5.0
        # 预览不写库：好感仍 5，原行未 reversed
        assert asyncio.run(svc.get("g1", "u1")).favor == 5
        assert asyncio.run(svc._storage.query_logs("g1", "u1"))[0]["reversed"] is False

    def test_undo_preview_errors(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError):
            asyncio.run(svc.undo_preview(999))  # 不存在


# ---------------- 一位小数工具 ----------------

class TestDecimal:
    def test_round1(self):
        assert round1(0.1 + 0.2) == 0.3  # 吸收浮点漂移
        assert round1(50.0) == 50.0
        assert round1(-3.0) == -3.0
        assert round1(3.14159) == 3.1  # 收敛到 1 位

    def test_fmt(self):
        assert fmt(50) == "50"
        assert fmt(50.0) == "50"
        assert fmt(50.5) == "50.5"
        assert fmt(-3.0) == "-3"


# ---------------- 时间衰减 ----------------


class TestDecay:
    def test_no_decay_when_disabled(self):
        assert effective_favor(80, 0, 1000, per_day=0, grace_days=3, baseline=0) == 80

    def test_no_decay_never_interacted(self):
        # updated_at=0 → 即便 idle 巨大也不衰减
        assert effective_favor(80, 0, 1_000_000_000, per_day=1, grace_days=3, baseline=0) == 80

    def test_within_grace_no_decay(self):
        now = 86400 * 10
        updated = now - 86400 * 2  # 2 天前，宽限 3 天内
        assert effective_favor(80, updated, now, per_day=1, grace_days=3, baseline=0) == 80

    def test_decays_beyond_grace(self):
        now = 86400 * 10
        updated = now - 86400 * 5  # 5 天前，超宽限 3 → 衰减 2 天
        assert effective_favor(80, updated, now, per_day=1, grace_days=3, baseline=0) == 78

    def test_does_not_cross_baseline(self):
        now = 86400 * 100
        updated = 86400  # 很久以前
        assert effective_favor(80, updated, now, per_day=100, grace_days=0, baseline=0) == 0

    def test_negative_rises_to_baseline(self):
        now = 86400 * 100
        updated = 86400
        assert effective_favor(-50, updated, now, per_day=100, grace_days=0, baseline=0) == 0


# ---------------- schema 迁移 ----------------

class TestMigration:
    def _build_v1_db(self, path):
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
CREATE TABLE favor(group_id TEXT,user_id TEXT,favor INTEGER NOT NULL DEFAULT 0,
                   updated_at REAL NOT NULL,PRIMARY KEY(group_id,user_id));
CREATE INDEX idx_favor_group ON favor(group_id,favor DESC);
CREATE TABLE daily_gain(group_id TEXT,user_id TEXT,day TEXT,
                        gain INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(group_id,user_id,day));
CREATE TABLE cooldown(group_id TEXT,user_id TEXT,key TEXT,last_ts REAL,
                      PRIMARY KEY(group_id,user_id,key));
"""
        )
        conn.execute("INSERT INTO favor VALUES('g','u',50,1.0)")
        conn.execute("INSERT INTO daily_gain VALUES('g','u','2026-07-24',3)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        return conn

    def test_v1_to_v2_round_trip(self, tmp_path):
        import sqlite3

        conn = self._build_v1_db(tmp_path / "old.db")
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

        # 列亲和升级为 REAL
        aff = conn.execute("PRAGMA table_info(favor)").fetchall()[2][2]
        assert aff == "REAL"

        # 旧数据无损、类型变 float
        fav = conn.execute(
            "SELECT favor FROM favor WHERE group_id='g' AND user_id='u'"
        ).fetchone()[0]
        gain = conn.execute(
            "SELECT gain FROM daily_gain WHERE group_id='g' AND user_id='u'"
        ).fetchone()[0]
        assert fav == 50.0 and isinstance(fav, float)
        assert gain == 3.0 and isinstance(gain, float)

        # 可继续写入小数与负值（v4 后 favor 多了 relationship 列，需显式指定列）
        conn.execute("INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES('g','u2',50.5,2.0)")
        conn.execute("INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES('g','u3',-30.0,3.0)")
        conn.commit()
        rows = conn.execute(
            "SELECT user_id,favor FROM favor WHERE group_id=? ORDER BY favor DESC", ("g",)
        ).fetchall()
        assert [r[0] for r in rows] == ["u2", "u", "u3"]
        conn.close()

    def test_fresh_db_is_latest(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        aff = conn.execute("PRAGMA table_info(daily_gain)").fetchall()[3][2]
        assert aff == "REAL"
        # v3：favor_log 流水表
        cols = [c[1] for c in conn.execute("PRAGMA table_info(favor_log)").fetchall()]
        assert "delta" in cols and "reason" in cols and "source" in cols
        conn.close()

    def test_v4_relationship_column(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = [c[1] for c in conn.execute("PRAGMA table_info(favor)").fetchall()]
        assert "relationship" in cols
        conn.close()

    def test_v5_nickname_column(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = [c[1] for c in conn.execute("PRAGMA table_info(favor)").fetchall()]
        assert "nickname" in cols
        conn.close()

    def test_v6_log_columns(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = [c[1] for c in conn.execute("PRAGMA table_info(favor_log)").fetchall()]
        assert "message" in cols and "reversed" in cols
        conn.close()

