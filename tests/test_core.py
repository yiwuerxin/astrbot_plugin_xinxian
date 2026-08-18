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

    def test_v7_impression_columns(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        migrate(conn)
        cols = [c[1] for c in conn.execute("PRAGMA table_info(favor)").fetchall()]
        assert "impression" in cols and "tags" in cols and "impression_at" in cols
        conn.close()

    def test_v6_to_v7_upgrade_round_trip(self, tmp_path):
        # 旧库（v6）升级到 v7：数据不丢，新列可用
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "old.db"))
        conn.execute("PRAGMA user_version(6)")
        # 构造 v6 结构的最小表（仅 favor）——真实库由 v1..v6 迁移生成
        conn.execute(
            "CREATE TABLE IF NOT EXISTS favor ("
            "group_id TEXT NOT NULL, user_id TEXT NOT NULL, favor REAL NOT NULL DEFAULT 0,"
            "updated_at REAL NOT NULL, relationship TEXT NOT NULL DEFAULT '',"
            "nickname TEXT NOT NULL DEFAULT '', PRIMARY KEY (group_id, user_id))"
        )
        conn.execute("INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES('g','u',5.5,1)")
        conn.commit()
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        row = conn.execute("SELECT favor, impression FROM favor WHERE group_id='g' AND user_id='u'").fetchone()
        assert row[0] == 5.5 and row[1] == ""
        conn.close()


# ---------------- 印象落库与读取 ----------------

class TestImpressionStorage:
    def _backend(self, tmp_path):
        b = SQLiteBackend(tmp_path / "t.db")
        asyncio.run(b.init())
        return b

    def test_set_and_read_impression(self, tmp_path):
        b = self._backend(tmp_path)
        asyncio.run(b.set_impression("g", "u", "嘴硬心软", ["毒舌", "夜猫子"]))
        rec = asyncio.run(b.get("g", "u"))
        assert rec.impression == "嘴硬心软"
        assert rec.parsed_tags() == ["毒舌", "夜猫子"]
        assert rec.impression_at > 0

    def test_set_tags_only_keeps_impression(self, tmp_path):
        # set_tags 走 set_impression（同列存储），手动改标签不丢印象
        b = self._backend(tmp_path)
        asyncio.run(b.set_impression("g", "u", "旧印象", ["a"]))
        from astrbot_plugin_xinxian.services.favor_service import FavorService

        svc = FavorService(b, LevelTable.from_config(None))
        asyncio.run(svc.set_tags("g", "u", ["手改"]))
        rec = asyncio.run(b.get("g", "u"))
        assert rec.impression == "旧印象" and rec.parsed_tags() == ["手改"]

    def test_standalone_include_impression(self, tmp_path):
        b = self._backend(tmp_path)
        asyncio.run(b.set_impression("g", "u", "测试印象", ["x"]))
        rows = asyncio.run(FavorService(
            b, LevelTable.from_config(None)
        ).standings("g"))
        assert rows and rows[0]["impression"] == "测试印象"
        assert rows[0]["tags"] == ["x"]


# ---------------- 评估提示词渲染（随人格同步） ----------------

class TestJudgePromptRender:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.judge_prompt import persona_block, render

        self.render = render
        self.persona_block = persona_block
        self.tpl = "你是「{persona_name}」。{persona_block}原话：「{text}」"

    def test_persona_name_injected(self):
        out = self.render(self.tpl, text="你好呀", persona_name="凛冬", persona_prompt="")
        assert "「凛冬」" in out and "原话：「你好呀」" in out
        assert "人设摘要" not in out  # 空人设 → 无摘要段

    def test_persona_block_rendered_and_truncated(self):
        long_prompt = "性格设定。" * 200  # 1000 字
        out = self.render(self.tpl, text="hi", persona_name="凛冬", persona_prompt=long_prompt)
        assert "人设摘要" in out
        assert long_prompt not in out  # 已截断
        assert self.persona_block(long_prompt).endswith("…\n\n")

    def test_fallback_name_when_empty(self):
        out = self.render(self.tpl, text="hi", persona_name="", persona_prompt="")
        assert "「小千」" in out  # 空名回落 bot_name

    def test_legacy_template_only_text_still_works(self):
        # 旧自定义模板只含 {text}：format 忽略多余 kwargs，不报错
        out = self.render("原话：「{text}」", text="hi", persona_name="凛冬", persona_prompt="x")
        assert out == "原话：「hi」"

    def test_persona_block_short_passthrough(self):
        assert self.persona_block("高冷") == "（人设摘要，供理解语境）：\n高冷\n\n"
        assert self.persona_block("") == ""
        assert self.persona_block("  ") == ""


# ---------------- 会话历史文本提取（上下文修复） ----------------

class TestJudgeContext:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.judge_context import extract_history_text

        self.extract = extract_history_text

    def test_string_passthrough(self):
        assert self.extract(" 你好 ") == "你好"

    def test_list_extracts_text_only(self):
        # AstrBot 4.26 assistant 真实格式：think + text 混合列表
        content = [
            {"type": "think", "think": "内心活动不应进入评审"},
            {"type": "text", "text": "？谁是你主人，别乱攀亲戚"},
        ]
        assert self.extract(content) == "？谁是你主人，别乱攀亲戚"

    def test_list_user_message(self):
        # 4.26 部分 user 消息也是列表
        content = [{"type": "text", "text": "[发送时间: …]\n另外一个人是什么鬼"}]
        assert "另外一个人是什么鬼" in self.extract(content)

    def test_multiple_text_segments_joined(self):
        content = [
            {"type": "text", "text": "第一段"},
            {"type": "image", "url": "http://x"},
            {"type": "text", "text": "第二段"},
        ]
        assert self.extract(content) == "第一段\n第二段"

    def test_empty_and_garbage(self):
        assert self.extract("") == ""
        assert self.extract([]) == ""
        assert self.extract(None) == ""
        assert self.extract([{"type": "image"}]) == ""
        assert self.extract(12345) == ""


# ---------------- 花名册渲染 ----------------

class TestRosterRender:
    def test_roster_block(self):
        from astrbot_plugin_xinxian.core.judge_prompt import render, roster_block

        assert roster_block("") == ""
        assert roster_block("  ") == ""
        r = roster_block("好m=1109841333（主人M的外号）")
        assert r.startswith("群成员花名册") and "好m=1109841333" in r

    def test_render_with_roster(self):
        from astrbot_plugin_xinxian.core.judge_prompt import render

        tpl = "评审。{roster}原话：「{text}」"
        out = render(tpl, text="hi", persona_name="小千", roster="好m=1109841333")
        assert "花名册" in out and "原话：「hi」" in out
        # 空花名册：段落自然消失
        out2 = render(tpl, text="hi", persona_name="小千", roster="")
        assert "花名册" not in out2


# ---------------- 印象与标签 ----------------

class TestImpression:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.impression import (
            build_summary_prompt, parse_summary, stats_tags,
        )

        self.stats_tags = stats_tags
        self.parse_summary = parse_summary
        self.build_prompt = build_summary_prompt

    def _logs(self, n, deltas, hours=None):
        out = []
        for i, d in enumerate(deltas):
            ts = 1000000000 + i * 60
            if hours:
                ts = time.mktime(time.strptime("2026-08-18 %02d:30" % hours[i % len(hours)], "%Y-%m-%d %H:%M"))
            out.append({"source": "judge", "delta": d, "ts": ts})
        return out

    def test_stats_tags_regular(self):
        logs = self._logs(10, [0.5] * 10)
        assert "常客" in self.stats_tags(logs)

    def test_stats_tags_night_owl(self):
        logs = self._logs(6, [0.5] * 6, hours=[2, 3, 1, 2, 14, 15])
        assert "夜猫子" in self.stats_tags(logs)

    def test_stats_tags_warm_and_snarky(self):
        warm = self._logs(5, [0.8, 0.6, 0.5, 0.8, 0.3])
        assert "热情" in self.stats_tags(warm)
        snarky = self._logs(5, [-1.0, -0.8, 0.5, -0.6, -0.9])
        assert "毒舌" in self.stats_tags(snarky)

    def test_stats_tags_empty(self):
        assert self.stats_tags([]) == []
        assert self.stats_tags([{"source": "admin", "delta": 5, "ts": 1}]) == []

    def test_parse_summary_normal(self):
        r = self.parse_summary("印象:嘴硬心软，爱用外号逗人\n标签:毒舌,夜猫子")
        assert r == ("嘴硬心软，爱用外号逗人", ["毒舌", "夜猫子"])

    def test_parse_summary_truncates_and_caps(self):
        r = self.parse_summary("印象:" + "长" * 200 + "\n标签:a,b,c,d,e，,f")
        assert len(r[0]) == 80 and len(r[1]) == 3

    def test_parse_summary_no_tag_line(self):
        r = self.parse_summary("印象:只是个路过的人")
        assert r == ("只是个路过的人", [])

    def test_parse_summary_wu_excluded(self):
        r = self.parse_summary("印象:神秘\n标签:无")
        assert r == ("神秘", [])

    def test_parse_summary_failure(self):
        assert self.parse_summary("我觉得这个人还行") is None
        assert self.parse_summary("印象:  \n标签:a") is None

    def test_build_prompt_content(self):
        p = self.build_prompt("M", "旧印象", ["+0.8 喜欢你 —— 直白好感"], "小千")
        assert "M" in p and "旧印象" in p and "+0.8" in p and "小千" in p
        p2 = self.build_prompt("M", "", [], "小千")
        assert "旧印象" not in p2 and "暂无记录" in p2


# ---------------- user_version 白名单写入 ----------------

class TestPragmaVersion:
    def test_set_and_reject(self):
        import sqlite3

        from astrbot_plugin_xinxian.storage.pragma_version import (
            SUPPORTED_VERSIONS,
            set_user_version,
        )

        conn = sqlite3.connect(":memory:")
        for v in SUPPORTED_VERSIONS:
            set_user_version(conn, v)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == v
        with pytest.raises(ValueError):
            set_user_version(conn, 99)
        conn.close()


# ---------------- 评审解析（五档 + 模型自由分值） ----------------

class TestJudgeParse:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.judge_parse import parse

        self.parse = parse

    def test_tier_score_evidence(self):
        r = self.parse("档位:热情\n分值:2.0\n证据:「跟你聊天比跟谁都开心」\n理由:直白好感")
        assert r.tier == "热情" and r.delta == 2.0  # 模型分值原样采用（区间内）
        assert "跟谁都开心" in r.evidence and r.reason == "直白好感"

    def test_score_clamped_to_tier_window(self):
        # 档位锚点=该档边界：友好上界 0.6；热情下界 1.8（上界交给 max_abs_delta）
        r = self.parse("档位:友好\n分值:1.5\n证据:「谢谢你帮我」")
        assert r.delta == 0.6
        r2 = self.parse("档位:热情\n分值:2.9\n证据:「爱你」")
        assert r2.delta == 2.9  # 热情区间 (1.8, 3.0]：2.9 保留
        r3 = self.parse("档位:热情\n分值:1.0\n证据:「爱你」")
        assert r3.delta == 1.8  # 低于热情下界 → 抬到 1.8

    def test_score_direction_mismatch_uses_tier(self):
        # 档位敌意但分值为正 → 以档位为准，取敌意区间值
        r = self.parse("档位:敌意\n分值:1.0\n证据:「蠢」")
        assert r.delta < 0

    def test_neutral_forces_zero(self):
        # 中性档位：分值强制 0（即使模型给了分）
        r = self.parse("档位:中性\n分值:0.5")
        assert r.tier == "中性" and r.delta == 0

    def test_nonneutral_without_evidence_forced_neutral(self):
        # 证据门槛：非中性档位没给证据 → 强制改判中性
        r = self.parse("档位:友好\n分值:0.8\n理由:语气不错")
        assert r.tier == "中性" and r.delta == 0

    def test_empty_evidence_forced_neutral(self):
        r = self.parse("档位:敌意\n分值:-2.0\n证据:  \n理由:x")
        assert r.tier == "中性" and r.delta == 0

    def test_score_missing_uses_tier_anchor(self):
        # 只给档位没给分值 → 用档位锚点
        r = self.parse("档位:友好\n证据:「谢谢你」")
        assert r.delta == 0.6

    def test_max_abs_clamp(self):
        # 敌意锚放宽到 -9 后区间变宽，-2.9 在区间内且在 max_abs=3 内 → 原样
        r = self.parse("档位:敌意\n分值:-2.9\n证据:「蠢」", {"敌意": -9}, max_abs_delta=3)
        assert r.delta == -2.9
        # max_abs=2 时被总上限钳制
        r2 = self.parse("档位:敌意\n分值:-2.9\n证据:「蠢」", {"敌意": -9}, max_abs_delta=2)
        assert r2.delta == -2.0

    def test_unknown_format_no_match(self):
        assert self.parse("我觉得还行吧") is None

    def test_legacy_format_friendly(self):
        # 旧协议兼容：态度+分值 → 档位定性、分值按区间钳制
        r = self.parse("态度:友好\n分值:0.8")
        assert r.tier == "友好" and 0 < r.delta <= 0.6

    def test_legacy_format_hostile(self):
        r = self.parse("态度:敌意\n分值:-2.9")
        assert r.tier == "敌意" and r.delta == -2.5

    def test_legacy_format_neutral(self):
        r = self.parse("态度:中性\n分值:0")
        assert r.tier == "中性" and r.delta == 0


# ---------------- 防通胀经济学层 ----------------

class TestLevelEconomy:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig, apply

        self.cfg = EconomyConfig()  # 默认：地板0.5 负重1.5 斜率0.25
        self.apply = apply

    def test_zero_passthrough(self):
        r = self.apply(0, "陌生", 0, self.cfg)
        assert r.delta == 0 and not r.floored

    def test_noise_floor(self):
        assert self.apply(0.3, "陌生", 0, self.cfg).delta == 0
        assert self.apply(-0.4, "陌生", 0, self.cfg).delta == 0
        r = self.apply(0.3, "陌生", 0, self.cfg)
        assert r.floored

    def test_noise_floor_disabled(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        cfg = EconomyConfig(noise_floor=0)
        assert self.apply(0.3, "陌生", 0, cfg).delta == 0.3

    def test_level_mult_positive_only(self):
        # 正分吃阶段乘数（乘后 round1 收敛：0.6×0.75=0.45→0.4 银行家舍入）
        assert self.apply(0.6, "陌生", 0, self.cfg).delta == 0.6   # mult=1
        assert self.apply(0.6, "友好", 0, self.cfg).delta == 0.4   # 0.45→0.4
        assert self.apply(1.8, "挚爱", 0, self.cfg).delta == 0.4   # 0.36→0.4

    def test_negative_weight_no_level_mult(self):
        # 负分吃负面权重、不吃阶段乘数：-0.8×1.5=-1.2
        r = self.apply(-0.8, "挚爱", 0, self.cfg)
        assert r.delta == -1.2 and r.multiplied

    def test_same_day_decay(self):
        # 第2次正分：1.8×(1-0.25×1)=1.35→1.4；第4次：×0.25=0.45→0.5（地板之上）
        assert self.apply(1.8, "陌生", 1, self.cfg).delta == 1.4
        assert self.apply(1.8, "陌生", 3, self.cfg).delta == 0.5
        assert self.apply(1.8, "陌生", 99, self.cfg).delta == 0.5  # 下限 0.25

    def test_same_day_decay_disabled(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        cfg = EconomyConfig(same_day_decay=0)
        assert self.apply(1.8, "陌生", 5, cfg).delta == 1.8

    def test_negative_not_decayed(self):
        # 同日衰减只作用于正分
        assert self.apply(-0.8, "陌生", 5, self.cfg).delta == -1.2

    def test_no_config_passthrough(self):
        assert self.apply(0.3, "陌生", 9, None).delta == 0.3  # 未启用 economy 原样通过

    def test_from_config_disabled_returns_none(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        assert EconomyConfig.from_config({"enabled": False}) is None
        eco = EconomyConfig.from_config({"noise_floor": 0.2, "level_mult": {"zhiai": 0.1}})
        assert eco.noise_floor == 0.2
        assert eco.level_mult["挚爱"] == 0.1 and eco.level_mult["挚友"] == 0.35  # 未给键回落默认


# ---------------- apply_judge 集成（economy 接入 FavorService） ----------------

class TestApplyJudgeEconomy:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig
        from astrbot_plugin_xinxian.storage.sqlite_backend import SQLiteBackend
        import tempfile

        self.dir = tempfile.mkdtemp()
        self.storage = SQLiteBackend(Path(self.dir) / "t.db")
        asyncio.run(self.storage.init())
        self.eco = EconomyConfig()

    def _svc(self, eco=True):
        return FavorService(
            self.storage, LevelTable.from_config(None),
            economy=self.eco if eco else None,
        )

    def test_noise_floor_zero_no_log(self):
        # 碎分（0.3 < 地板 0.5）被拦截：无变动、无流水
        svc = self._svc()
        ch = asyncio.run(svc.apply_judge("g", "u1", 0.3, message="你好"))
        assert ch.delta == 0 and ch.clamped
        assert asyncio.run(self.storage.query_logs("g", "u1")) == []

    def test_level_mult_applied(self):
        # 高阶段正分吃乘数：先设到挚爱段（95+），热情 1.8×0.2=0.36→0.4
        svc = self._svc()
        asyncio.run(svc.set_favor("g", "u2", 96))
        ch = asyncio.run(svc.apply_judge("g", "u2", 1.8, message="爱你！"))
        assert ch.delta == 0.4

    def test_negative_weight_applied(self):
        # 负分吃 1.5 倍权重：冷淡 -0.8×1.5=-1.2
        svc = self._svc()
        ch = asyncio.run(svc.apply_judge("g", "u3", -0.8, message="哦"))
        assert ch.delta == -1.2

    def test_same_day_decay_sequence(self):
        # 同日连续正向：热情 1.8 → 1.4（×0.75）→ 0.9（×0.5）逐次递减
        svc = self._svc()
        d1 = asyncio.run(svc.apply_judge("g", "u4", 1.8)).delta
        d2 = asyncio.run(svc.apply_judge("g", "u4", 1.8)).delta
        d3 = asyncio.run(svc.apply_judge("g", "u4", 1.8)).delta
        assert (d1, d2, d3) == (1.8, 1.4, 0.9)

    def test_economy_off_passthrough(self):
        svc = self._svc(eco=False)
        ch = asyncio.run(svc.apply_judge("g", "u5", 0.3, message="你好"))
        assert ch.delta == 0.3  # 未启用经济学层：碎分直给（旧行为）

