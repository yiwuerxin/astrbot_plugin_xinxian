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

# CI 等无 AstrBot 的环境：注入最小桩 astrbot.api.logger——本仓库测试
# 契约是离线可跑（CLAUDE.md），taskregistry 模块级与服务层 lazy import
# 的 logger 需要它（容器内有真实 astrbot 时不生效，仍用真 logger）
try:
    from astrbot.api import logger  # noqa: F401
except ImportError:
    import sys as _sys
    import types as _types

    class _OfflineLogger:
        def __getattr__(self, name):
            return lambda *a, **k: None

    _pkg = _types.ModuleType("astrbot")
    _api = _types.ModuleType("astrbot.api")
    _api.logger = _OfflineLogger()
    _pkg.api = _api
    _sys.modules.setdefault("astrbot", _pkg)
    _sys.modules.setdefault("astrbot.api", _api)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_xinxian.core.config_migrations import (
    migrate_saved_defaults,
)  # noqa: E402
from astrbot_plugin_xinxian.core.decay import effective_favor  # noqa: E402
from astrbot_plugin_xinxian.core.decimal import fmt, round1  # noqa: E402
from astrbot_plugin_xinxian.core.identity import (
    is_master,
    parse_master_ids,
)  # noqa: E402
from astrbot_plugin_xinxian.core.levels import LevelTable  # noqa: E402
from astrbot_plugin_xinxian.core.relationship import RelationshipTable  # noqa: E402
from astrbot_plugin_xinxian.core.models import FavorRecord  # noqa: E402
from astrbot_plugin_xinxian.services.favor_service import FavorService  # noqa: E402
from astrbot_plugin_xinxian.storage.migrations import (
    SCHEMA_VERSION,
    migrate,
)  # noqa: E402
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

    def test_master_guidance_defaults(self):
        # 主人版指引语义按正负分界：负值＝闹别扭；正值＝正面关系的亲疏程度
        assert "别扭" in self.table.guidance_of(-50, master=True)  # 厌恶（负值才别扭）
        assert "生分" in self.table.guidance_of(5, master=True)  # 陌生：生分但不生气
        assert "不生气" in self.table.guidance_of(5, master=True)
        assert "温和亲近" in self.table.guidance_of(15.0, master=True)  # 认识：正面升温
        assert "撒娇" in self.table.guidance_of(40, master=True)  # 友好
        assert "黏人" in self.table.guidance_of(60, master=True)  # 亲密
        assert "护主人" in self.table.guidance_of(85, master=True)  # 挚友
        assert "毫无保留" in self.table.guidance_of(99, master=True)  # 挚爱
        # 正值低段不得出现冲突叙事（别扭/冷战期/和好）；「不冷战」这类明确否定除外
        for favor in (5, 15.0, 40):
            g = self.table.guidance_of(favor, master=True)
            assert "别扭" not in g and "冷战期" not in g and "和好" not in g

    def test_master_guidance_off_for_normal_members(self):
        # 非主人：master=False 用普通指引，不含主人语义
        assert self.table.guidance_of(14.6, master=False) == "友善客气，像刚认识的朋友"
        assert "主人" not in self.table.guidance_of(60, master=False)

    def test_master_guidance_custom(self):
        table = LevelTable.from_config(
            {"levels": {"renshi": {"master_guidance": "主人的专属冷战文案"}}}
        )
        assert table.guidance_of(20, master=True) == "主人的专属冷战文案"
        # 未自定义的等级回落默认主人指引
        assert "黏人" in table.guidance_of(60, master=True)

    def test_disclosure_defaults(self):
        # 表露分寸随等级递进：浅层不袒露，深层袒露心底话
        assert "不袒露" in self.table.disclosure_of(-50)  # 厌恶
        assert "不主动" in self.table.disclosure_of(5)  # 陌生
        assert "日常小事" in self.table.disclosure_of(15.0)  # 认识
        assert "趣事" in self.table.disclosure_of(40)  # 友好
        assert "倾诉" in self.table.disclosure_of(60)  # 亲密
        assert "心底话" in self.table.disclosure_of(85)  # 挚友
        assert "毫无保留" in self.table.disclosure_of(99)  # 挚爱

    def test_disclosure_custom(self):
        table = LevelTable.from_config(
            {"levels": {"zhiai": {"disclosure": "自定义的表露文案"}}}
        )
        assert table.disclosure_of(99) == "自定义的表露文案"
        assert "倾诉" in table.disclosure_of(60)  # 未自定义等级回落默认


# ---------------- 身份 ----------------


class TestIdentity:
    def test_parse(self):
        assert parse_master_ids("123456789, 222，333") == ["123456789", "222", "333"]
        assert parse_master_ids("") == []

    def test_is_master(self):
        assert is_master("123456789", ["123456789"])
        assert not is_master("999", ["123456789"])


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
    def test_template_validation(self):
        # X2：自定义模板未知占位符必须在装配期发现（format 时 KeyError 会
        # 打断每次 LLM 请求的注入——注入是每次对话的必经路径，不是增值功能）
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        assert InjectService.validate_template("好感 {favor}（{level_name}）") is None
        # 全占位符的合法模板
        full = (
            "{"
            + "}{".join(
                [
                    "nickname",
                    "user_id",
                    "master_line",
                    "favor",
                    "max_favor",
                    "level_name",
                    "level_guidance",
                    "disclosure",
                    "interaction",
                    "recent_events",
                    "relationship",
                    "impression",
                    "milestone",
                ]
            )
            + "}"
        )
        assert InjectService.validate_template(full) is None
        # 未知占位符 / 位置参数 / 转义大括号（合法）
        err = InjectService.validate_template("JSON 示例 {foo}")
        assert err and "foo" in err
        assert InjectService.validate_template("位置参数 {}") is not None
        assert InjectService.validate_template("字面大括号 {{ok}}") is None

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
        assert "你们的关系" not in inj.build_block(
            FavorRecord("g", "u", 80), is_master=False
        )

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
        assert "钉住" not in inj0.build_block(
            FavorRecord("g", "u", 50), is_master=False
        )

    def test_block_master_line_default(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(LevelTable.from_config(None), "档案：{favor}{master_line}")
        block = inj.build_block(FavorRecord("g", "u", 50), is_master=True)
        # 默认措辞要点：身份恒定 + 好感照常涨跌 + 负好感闹别扭声明 + 称谓替换
        assert "主人身份恒定" in block
        assert "闹别扭" in block
        assert "你的主人" in block  # {master_title} 替换为默认"主人"
        # 非主人不出现主人提示
        assert "主人身份恒定" not in inj.build_block(
            FavorRecord("g", "u", 50), is_master=False
        )

    def test_block_master_line_custom(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(
            LevelTable.from_config(None),
            "档案：{favor}{master_line}",
            master_prompt="，{master_title}大人请受我一拜",
        )
        block = inj.build_block(FavorRecord("g", "u", 50), is_master=True)
        assert "，主人大人请受我一拜" in block  # {master_title} 替换

    def test_block_master_guidance_switch(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(LevelTable.from_config(None), "{level_guidance}")
        rec = FavorRecord("g", "u", 15.0)  # 认识级（正值）
        block_m = inj.build_block(rec, is_master=True)
        assert "温和亲近" in block_m  # 主人版指引：正面语义
        assert "像刚认识的朋友" not in block_m  # 不再照搬外人措辞
        assert "别扭" not in block_m  # 正值不得有冲突叙事
        # 非主人保持原文指引
        assert "像刚认识的朋友" in inj.build_block(rec, is_master=False)

    def test_block_disclosure_placeholder(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        # 含占位符：渲染表露分寸
        inj = InjectService(LevelTable.from_config(None), "- 表露分寸：{disclosure}")
        block = inj.build_block(FavorRecord("g", "u", 40), is_master=False)
        assert "趣事" in block
        # 自定义模板无占位符：不报错、不渲染（与 {recent_events} 同语义）
        inj2 = InjectService(LevelTable.from_config(None), "档案：{favor}")
        b2 = inj2.build_block(FavorRecord("g", "u", 40), is_master=False)
        assert "表露" not in b2


# ---------------- 好感度增减（内存级 SQLite） ----------------


def _make_service(tmp_path, **kw) -> FavorService:
    storage = SQLiteBackend(tmp_path / "test.db")
    asyncio.run(storage.init())
    defaults = dict(
        max_favor=100,
        min_favor=-100,
        default_favor=0,
        daily_cap_up=15,
        daily_cap_down=15,
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
        assert ch.delta == 0.2  # 0.2 在 0.3 额度内，全额生效
        assert ch.clamped is False  # 不应被浮点噪声误判为截断

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
        assert (
            asyncio.run(svc.recent_events("g2", "u1", count=3, days=7)) == []
        )  # 每群独立

    def test_recent_events_significance_weighting(self, tmp_path):
        # 显著事件（|delta|≥阈值）记忆窗口延长：10 天前的大冲突仍在 7 天窗口外、3× 窗口内
        import time as _t

        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 0.4, reason="寒暄", source="judge"))
        logs = asyncio.run(svc._storage.query_logs("g1", "u1"))
        # 直接改库模拟 10 天前的显著事件
        old_ts = _t.time() - 10 * 86400
        with svc._storage._lock:
            svc._storage._c().execute(
                "insert into favor_log (group_id,user_id,delta,favor_before,favor_after,reason,source,ts) "
                "values ('g1','u1',-2.5,0,-2.5,'重骂','judge',?)",
                (old_ts,),
            )
            svc._storage._c().commit()
        ev = asyncio.run(
            svc.recent_events(
                "g1", "u1", count=3, days=7, sig_threshold=1.0, sig_window_mult=3.0
            )
        )
        deltas = [r["delta"] for r in ev]
        assert -2.5 in deltas  # 10 天前的大冲突仍在 3× 记忆窗口内
        assert 0.4 in deltas  # 刚发生的普通事件当然也在
        ev_default = asyncio.run(svc.recent_events("g1", "u1", count=3, days=7))
        assert -2.5 not in [
            r["delta"] for r in ev_default
        ]  # 未开启加权时 10 天前已淡忘

    def test_recent_events_skips_reversed(self, tmp_path):
        # 已撤销的变动不作为记忆注入
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 1.5, reason="夸", source="judge"))
        with svc._storage._lock:
            svc._storage._c().execute(
                "update favor_log set reversed = 1 where group_id='g1' and user_id='u1'"
            )
            svc._storage._c().commit()
        assert asyncio.run(svc.recent_events("g1", "u1", count=3, days=7)) == []

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
        # X1：默认精确匹配（内部路径——同日衰减/修复期/近期印象/印象汇总/
        # 里程碑/成员详情全部语义要求精确，LIKE '%..%' 会让 QQ 互为子串时
        # 把别人的流水算进本人记忆）；模糊仅 WebUI 搜索显式 fuzzy=True
        rows = asyncio.run(svc._storage.query_logs("g1", "123456"))
        assert [r["user_id"] for r in rows] == ["123456"]
        fuzzy = asyncio.run(svc._storage.query_logs("g1", "123", fuzzy=True))
        assert [r["user_id"] for r in fuzzy] == ["123456"]

    def test_logs_exact_no_cross_user_pollution(self, tmp_path):
        # X1 回归：QQ 123456 是 1234567 的子串——精确路径不得串数据
        svc = _make_service(tmp_path, daily_cap_up=200)
        asyncio.run(svc.change("g1", "1234567", 1, source="api"))
        asyncio.run(svc.change("g1", "123456", 1, source="api"))
        rows = asyncio.run(svc._storage.query_logs("g1", "1234567"))
        assert [r["user_id"] for r in rows] == ["1234567"]
        # 服务层内部读（recent_events 默认查询）同样不得混入他人流水
        events = asyncio.run(svc.recent_events("g1", "1234567", 3, 7))
        assert events and all(r["user_id"] == "1234567" for r in events)

    def test_distinct_groups(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.set_favor("g1", "u1", 50))
        asyncio.run(svc.set_favor("g2", "u1", 50))
        asyncio.run(svc.set_favor("g1", "u2", 50))
        gmap = {
            g["group_id"]: g["count"]
            for g in asyncio.run(svc._storage.distinct_groups())
        }
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
        asyncio.run(
            svc._storage.add_log(
                "g1", "u1", 1.0, 0.0, 1.0, "夸", "judge", 0.0, message="你好呀"
            )
        )
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert rows[0]["message"] == "你好呀"
        assert rows[0]["reversed"] is False

    def test_log_truncates_long_text(self, tmp_path):
        # 数据最小化：message/reason 落库前截断到 200 字符（存储层兜底所有调用路径）
        svc = _make_service(tmp_path)
        long_msg, long_reason = "很" * 500, "理" * 300
        asyncio.run(
            svc._storage.add_log(
                "g1", "u1", 1.0, 0.0, 1.0, long_reason, "judge", 0.0, message=long_msg
            )
        )
        rows = asyncio.run(svc._storage.query_logs("g1", "u1"))
        assert len(rows[0]["message"]) == 200
        assert len(rows[0]["reason"]) == 200

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

    def test_undo_single_transaction_semantics(self, tmp_path):
        # X4：撤销走 storage.apply_undo 单事务——effective 回调（衰减读值投影）
        # 被采纳、undo 流水带 撤销#id 理由、原行 reversed 同事务落定、
        # 钳到边界 real=0 时不追加零流水但照样标记
        svc = _make_service(tmp_path, max_favor=100, min_favor=-100)
        asyncio.run(svc.change("g", "u", 5, source="api"))
        log_id = asyncio.run(svc._storage.query_logs("g", "u"))[0]["id"]
        seen: list[float] = []

        def eff(stored, ts, h):
            seen.append(stored)
            return stored - 2.0  # 模拟两天衰减读值（纯函数投影）

        info = asyncio.run(
            svc._storage.apply_undo(
                log_id, max_favor=100, min_favor=-100, effective=eff
            )
        )
        assert seen == [5.0] and info["before"] == 3.0
        # 撤销 +5 的流水：有效值 3 − 5 = −2，实际变动 −5
        assert info["after"] == -2.0 and info["delta"] == -5.0
        rows = asyncio.run(svc._storage.query_logs("g", "u"))
        assert rows[0]["source"] == "undo" and rows[0]["reason"] == f"撤销#{log_id}"
        assert rows[0]["reversed"] is False and rows[1]["reversed"] is True
        # 钳边界：把行设到 max，撤销一条 -5（反向 +5 已无处可去）→ 不追加零流水
        asyncio.run(svc.set_favor("g", "u", 100))
        neg_id = None
        with svc._storage._lock:
            svc._storage._c().execute(
                "INSERT INTO favor_log(group_id,user_id,delta,favor_before,"
                "favor_after,reason,source,ts) VALUES('g','u',-5,105,100,'r','api',1)"
            )
            svc._storage._c().commit()
            neg_id = (
                svc._storage._c().execute("SELECT last_insert_rowid()").fetchone()[0]
            )
        before_count = len(asyncio.run(svc._storage.query_logs("g", "u")))
        info2 = asyncio.run(
            svc._storage.apply_undo(neg_id, max_favor=100, min_favor=-100)
        )
        assert info2["delta"] == 0.0 and info2["after"] == 100.0
        after_rows = asyncio.run(svc._storage.query_logs("g", "u"))
        assert len(after_rows) == before_count  # 零流水未追加
        orig = next(r for r in after_rows if r["id"] == neg_id)
        assert orig["reversed"] is True  # 但原行已标记

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
        asyncio.run(svc.undo_log(orig_id))  # favor 0
        undo_id = asyncio.run(svc._storage.query_logs("g1", "u1"))[0]["id"]
        asyncio.run(svc.undo_log(undo_id))  # 撤销 undo = 重做
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
    """指数遗忘曲线：effective = baseline + (stored−baseline)·0.5^(闲置天数/h)"""

    def test_no_decay_never_interacted(self):
        # updated_at=0 → 即便 idle 巨大也不衰减
        assert effective_favor(80, 0, 1_000_000_000, half_life=10, baseline=0) == 80

    def test_short_idle_small_loss(self):
        # h=10、静默 1 天：80 → 80×0.5^0.1 ≈ 74.6（连续衰减，无宽限断崖）
        now = 86400 * 10
        updated = now - 86400 * 1
        assert effective_favor(80, updated, now, half_life=10, baseline=0) == 74.6

    def test_half_life_meaning(self):
        # h=10、静默恰 10 天 → 恰好减半（半衰期定义）
        now = 86400 * 100
        updated = now - 86400 * 10
        assert effective_favor(80, updated, now, half_life=10, baseline=0) == 40

    def test_consolidated_decays_slower(self):
        # 老朋友 h=60：静默 30 天保留 70%；新关系 h=10 只剩 12.5%
        now = 86400 * 100
        updated = now - 86400 * 30
        assert effective_favor(80, updated, now, half_life=60, baseline=0) == round(
            80 * 0.5**0.5, 1
        )
        assert effective_favor(80, updated, now, half_life=10, baseline=0) == 10.0

    def test_baseline_convergence_no_cross(self):
        # 极长静默：两侧存量都收敛到 baseline 不越界
        now = 86400 * 10000
        updated = 86400
        assert effective_favor(80, updated, now, half_life=10, baseline=0) == 0
        assert effective_favor(-50, updated, now, half_life=10, baseline=0) == 0
        # 非零基线同理
        assert effective_favor(80, updated, now, half_life=10, baseline=20) == 20

    def test_illegal_half_life_falls_back(self):
        now = 86400 * 100
        updated = now - 86400 * 10
        # 非法 h → 回落 HALF_LIFE_MIN(5)，静默 10 天=两个半衰期 → 1/4
        assert effective_favor(80, updated, now, half_life=0, baseline=0) == 20
        assert effective_favor(80, updated, now, half_life=-3, baseline=0) == 20

    def test_future_or_zero_time_noop(self):
        assert effective_favor(80, 1000, 1000, half_life=10, baseline=0) == 80
        assert (
            effective_favor(80, 2000, 1000, half_life=10, baseline=0) == 80
        )  # 时间倒流防御


class TestConsolidate:
    """巩固规则（SM-2 温和版）：正向 ×1.3 封顶 60；非正向不动"""

    def test_positive_grows(self):
        from astrbot_plugin_xinxian.core.decay import consolidate_half_life as ch

        assert ch(10, base=10, growth=1.3, h_max=60, positive=True) == 13.0
        assert ch(13, base=10, growth=1.3, h_max=60, positive=True) == 16.9

    def test_cap_at_max(self):
        from astrbot_plugin_xinxian.core.decay import consolidate_half_life as ch

        assert ch(50, base=10, growth=1.3, h_max=60, positive=True) == 60.0
        assert ch(60, base=10, growth=1.3, h_max=60, positive=True) == 60.0

    def test_nonpositive_unchanged(self):
        from astrbot_plugin_xinxian.core.decay import consolidate_half_life as ch

        assert ch(30, base=10, growth=1.3, h_max=60, positive=False) == 30.0
        assert (
            ch(30, base=10, growth=1.3, h_max=60, positive=False) == 30.0
        )  # 负向也不降

    def test_illegal_falls_back_to_base(self):
        from astrbot_plugin_xinxian.core.decay import consolidate_half_life as ch

        assert ch(0, base=10, growth=1.3, h_max=60, positive=True) == 13.0
        assert ch(-5, base=10, growth=1.3, h_max=60, positive=False) == 10.0

    def test_sequence_daily_chat(self):
        # 每天一次正向互动的半衰期序列：10→13→16.9→21.9…→60 封顶
        from astrbot_plugin_xinxian.core.decay import consolidate_half_life as ch

        h = 10.0
        seq = [h]
        for _ in range(20):
            h = ch(h, base=10, growth=1.3, h_max=60, positive=True)
            seq.append(h)
        assert seq[-1] == 60.0 and all(x <= 60 for x in seq)


# ---------------- schema 迁移 ----------------


class TestMigration:
    def _build_v1_db(self, path):
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.executescript("""
CREATE TABLE favor(group_id TEXT,user_id TEXT,favor INTEGER NOT NULL DEFAULT 0,
                   updated_at REAL NOT NULL,PRIMARY KEY(group_id,user_id));
CREATE INDEX idx_favor_group ON favor(group_id,favor DESC);
CREATE TABLE daily_gain(group_id TEXT,user_id TEXT,day TEXT,
                        gain INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(group_id,user_id,day));
CREATE TABLE cooldown(group_id TEXT,user_id TEXT,key TEXT,last_ts REAL,
                      PRIMARY KEY(group_id,user_id,key));
""")
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
        conn.execute(
            "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES('g','u2',50.5,2.0)"
        )
        conn.execute(
            "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES('g','u3',-30.0,3.0)"
        )
        conn.commit()
        rows = conn.execute(
            "SELECT user_id,favor FROM favor WHERE group_id=? ORDER BY favor DESC",
            ("g",),
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

    def test_v6_to_v8_upgrade_round_trip(self, tmp_path):
        # 旧库（v6）升级到最新：数据不丢，新列可用
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
        conn.execute(
            "INSERT INTO favor(group_id, user_id, favor, updated_at) VALUES('g','u',5.5,1)"
        )
        conn.commit()
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        row = conn.execute(
            "SELECT favor, impression, half_life FROM favor WHERE group_id='g' AND user_id='u'"
        ).fetchone()
        assert row[0] == 5.5 and row[1] == "" and row[2] == 10  # 存量统一 h=10
        conn.close()

    def test_v8_half_life_column(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "fresh.db"))
        migrate(conn)
        cols = [c[1] for c in conn.execute("PRAGMA table_info(favor)").fetchall()]
        assert "half_life" in cols
        conn.close()


# ---------------- 衰减写路径集成（apply_delta 巩固） ----------------


class TestDecayWritePath:
    def _svc(self, tmp_path, decay=True):
        b = SQLiteBackend(tmp_path / "t.db")
        asyncio.run(b.init())
        return (
            FavorService(
                b,
                LevelTable.from_config(None),
                decay_enabled=decay,
                half_life_base=10,
                half_life_growth=1.3,
                half_life_max=60,
            ),
            b,
        )

    def test_positive_delta_consolidates(self, tmp_path):
        svc, b = self._svc(tmp_path)
        asyncio.run(svc.change("g", "u", 5))
        rec = asyncio.run(b.get("g", "u"))
        assert rec.half_life == 13.0  # 10 × 1.3

    def test_negative_delta_keeps_half_life(self, tmp_path):
        svc, b = self._svc(tmp_path)
        asyncio.run(svc.change("g", "u", 5))  # h → 13
        asyncio.run(svc.change("g", "u", -1))  # 负向不动 h
        rec = asyncio.run(b.get("g", "u"))
        assert rec.half_life == 13.0

    def test_set_favor_does_not_consolidate(self, tmp_path):
        # 管理员设定不算感情互动
        svc, b = self._svc(tmp_path)
        asyncio.run(svc.set_favor("g", "u", 50))
        rec = asyncio.run(b.get("g", "u"))
        assert rec.half_life == 10.0

    def test_decay_disabled_no_consolidation(self, tmp_path):
        svc, b = self._svc(tmp_path, decay=False)
        asyncio.run(svc.change("g", "u", 5))
        rec = asyncio.run(b.get("g", "u"))
        assert rec.half_life == 10.0


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
        from astrbot_plugin_xinxian.services.impression_service import ImpressionService

        svc = ImpressionService(b)
        asyncio.run(svc.set_tags("g", "u", ["手改"]))
        rec = asyncio.run(b.get("g", "u"))
        assert rec.impression == "旧印象" and rec.parsed_tags() == ["手改"]

    def test_impression_service_refresh_without_summarizer(self, tmp_path):
        # 未绑定 summarizer（评审关闭/未装配）时立即刷新给出明确失败，不抛异常
        from astrbot_plugin_xinxian.services.impression_service import ImpressionService

        svc = ImpressionService(self._backend(tmp_path))
        ok, msg = asyncio.run(svc.refresh_now("g", "u"))
        assert ok is False and "未启用" in msg

    def test_impression_service_maybe_refresh_silent(self, tmp_path):
        # maybe_refresh 在无流水/无 summarizer 时静默无动作（增值功能不抛错）
        from astrbot_plugin_xinxian.services.impression_service import ImpressionService

        b = self._backend(tmp_path)
        svc = ImpressionService(b)
        asyncio.run(svc.maybe_refresh("g", "u"))  # 不应抛异常
        assert asyncio.run(b.get("g", "u")) is None

    def test_impression_service_llm_timeout(self, tmp_path):
        # X3：provider 挂起时按失败降级（超时而不是永久滞留），结果为 (False, …)
        import asyncio as _aio
        from astrbot_plugin_xinxian.services.impression_service import ImpressionService

        b = self._backend(tmp_path)
        asyncio.run(b.set_impression("g", "u", "旧", ["a"]))
        for i in range(3):  # 造出评审流水，走到 LLM 调用分支
            asyncio.run(b.add_log("g", "u", 0.5, 0, 0.5, "r", "judge", 1000 + i))

        class _HangProvider:
            async def text_chat(self, prompt=None, **kw):
                await _aio.sleep(30)

        class _FakeJudge:
            async def resolve_display_name(self, umo=""):
                return "小千"

            async def resolve_summary_provider(self):
                return _HangProvider()

        svc = ImpressionService(b, summarizer=_FakeJudge(), timeout_sec=0.05)
        ok, msg = asyncio.run(svc.refresh_now("g", "u"))
        assert ok is False and "刷新失败" in msg
        rec = asyncio.run(b.get("g", "u"))
        assert rec.impression == "旧"  # 超时不清掉原印象

    def test_standalone_include_impression(self, tmp_path):
        b = self._backend(tmp_path)
        asyncio.run(b.set_impression("g", "u", "测试印象", ["x"]))
        rows = asyncio.run(FavorService(b, LevelTable.from_config(None)).standings("g"))
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
        out = self.render(
            self.tpl, text="你好呀", persona_name="凛冬", persona_prompt=""
        )
        assert "「凛冬」" in out and "原话：「你好呀」" in out
        assert "人设摘要" not in out  # 空人设 → 无摘要段

    def test_persona_block_rendered_and_truncated(self):
        long_prompt = "性格设定。" * 200  # 1000 字
        out = self.render(
            self.tpl, text="hi", persona_name="凛冬", persona_prompt=long_prompt
        )
        assert "人设摘要" in out
        assert long_prompt not in out  # 已截断
        assert self.persona_block(long_prompt).endswith("…\n\n")

    def test_fallback_name_when_empty(self):
        out = self.render(self.tpl, text="hi", persona_name="", persona_prompt="")
        assert "「小千」" in out  # 空名回落 bot_name

    def test_legacy_template_only_text_still_works(self):
        # 旧自定义模板只含 {text}：format 忽略多余 kwargs，不报错
        out = self.render(
            "原话：「{text}」", text="hi", persona_name="凛冬", persona_prompt="x"
        )
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
        r = roster_block("阿狸=123456789（群友外号示例）")
        assert r.startswith("群成员花名册") and "阿狸=123456789" in r

    def test_render_with_roster(self):
        from astrbot_plugin_xinxian.core.judge_prompt import render

        tpl = "评审。{roster}原话：「{text}」"
        out = render(tpl, text="hi", persona_name="小千", roster="阿狸=123456789")
        assert "花名册" in out and "原话：「hi」" in out
        # 空花名册：段落自然消失
        out2 = render(tpl, text="hi", persona_name="小千", roster="")
        assert "花名册" not in out2


# ---------------- 印象与标签 ----------------


class TestImpressionPoints:
    """P-C 带权印象点模型（纯逻辑 + 存储 v9 + 迁移）。"""

    def test_points_pure_logic(self):
        import time
        import random
        from astrbot_plugin_xinxian.core.impression_points import (
            anonymize,
            loss_aversion_multiplier,
            merge_points,
            parse_points,
            render_impression,
            retain,
            time_weight,
        )

        now = time.time()
        merged = merge_points(
            [{"point": "嘴硬心软爱用外号逗人", "weight": 5, "ts": now}],
            [{"point": "嘴硬心软爱用外号逗大家", "weight": 4}],
        )
        assert len(merged) == 1 and merged[0]["weight"] == 9  # 相似合并权重求和
        assert (
            len(
                merge_points(
                    [],
                    [{"point": "a", "weight": 5}, {"point": "完全不同", "weight": 3}],
                )
            )
            == 2
        )
        assert [
            time_weight(x) for x in (60, 7200, 3 * 86400, 10 * 86400, 40 * 86400)
        ] == [1.0, 0.7, 0.95, 0.1, 0.05]
        pts = [{"point": f"p{i}", "weight": 1, "ts": now} for i in range(14)]
        kept, dropped = retain(pts, now, rng=random.Random(1))
        assert (len(kept), len(dropped)) == (10, 4)  # 加权随机限量保留
        assert (
            loss_aversion_multiplier(-2) == 1.5 and loss_aversion_multiplier(1) == 1.0
        )
        assert anonymize("阿狸骂了小咕嘎", ["阿狸", "小咕嘎"]) == "用户A骂了用户B"
        assert parse_points('好的 [{"point":"爱抬杠","weight":7}]') == [
            {"point": "爱抬杠", "weight": 7}
        ]
        assert parse_points("拒答") is None
        assert render_impression([{"point": "爱抬杠", "weight": 9}]) == "爱抬杠"

    def test_points_storage_v9(self, tmp_path):
        b = SQLiteBackend(tmp_path / "t.db")
        asyncio.run(b.init())
        rec = asyncio.run(b.set_value("g", "u", 10))
        asyncio.run(b.set_points("g", "u", [{"point": "话痨", "weight": 6, "ts": 1.0}]))
        rec = asyncio.run(b.get("g", "u"))
        assert rec.parsed_points() == [{"point": "话痨", "weight": 6, "ts": 1.0}]
        rows = asyncio.run(b.list_favor("g"))
        assert rows[0].parsed_points()[0]["point"] == "话痨"

    def test_v9_migration_roundtrip(self, tmp_path):
        # 旧 v8 库升级到 v9：加 points 列，既有数据不动
        import sqlite3

        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA user_version(8)")
        conn.execute("""CREATE TABLE favor (
            group_id TEXT NOT NULL, user_id TEXT NOT NULL, favor REAL NOT NULL,
            updated_at REAL NOT NULL, relationship TEXT NOT NULL DEFAULT '',
            nickname TEXT NOT NULL DEFAULT '', impression TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '', impression_at REAL NOT NULL DEFAULT 0,
            half_life REAL NOT NULL DEFAULT 10, PRIMARY KEY (group_id, user_id))""")
        conn.execute(
            "INSERT INTO favor(group_id,user_id,favor,updated_at) VALUES('g','u',5.5,1)"
        )
        conn.commit()
        conn.close()
        b = SQLiteBackend(db)
        asyncio.run(b.init())
        rec = asyncio.run(b.get("g", "u"))
        assert rec.favor == 5.5 and rec.parsed_points() == []  # 新列默认空

    def test_service_query_logs_passthrough(self, tmp_path):
        # Sourcery 回归：面板 /logs 走 FavorService.query_logs——签名须带 offset/fuzzy
        svc = _make_service(tmp_path, daily_cap_up=200)
        for i in range(3):
            asyncio.run(svc.change("g", f"u{i}", 1, source="api"))
        rows = asyncio.run(svc.query_logs("g", "u1", limit=10, offset=0, fuzzy=False))
        assert [r["user_id"] for r in rows] == ["u1"]  # 精确默认不混 u0/u10
        all_rows = asyncio.run(svc.query_logs("g", "u", limit=10, fuzzy=True))
        assert len(all_rows) == 3  # 模糊可选

    def test_storage_set_profile_atomic(self, tmp_path):
        # Sourcery 回归：点集/印象/标签一次 upsert 写入
        b = SQLiteBackend(tmp_path / "t.db")
        asyncio.run(b.init())
        asyncio.run(b.set_value("g", "u", 5))
        asyncio.run(
            b.set_profile(
                "g",
                "u",
                "嘴硬心软",
                ["毒舌"],
                [{"point": "爱抬杠", "weight": 7, "ts": 1.0}],
            )
        )
        rec = asyncio.run(b.get("g", "u"))
        assert rec.impression == "嘴硬心软" and rec.parsed_tags() == ["毒舌"]
        assert rec.parsed_points() == [{"point": "爱抬杠", "weight": 7, "ts": 1.0}]

    def test_service_points_mode_offline(self, tmp_path):
        # points_mode 关闭：走 legacy 一句话路径（既有行为不变）
        from astrbot_plugin_xinxian.services.impression_service import ImpressionService

        b = SQLiteBackend(tmp_path / "t.db")
        asyncio.run(b.init())
        svc = ImpressionService(b)
        ok, msg = asyncio.run(svc.refresh_now("g", "u"))
        assert ok is False and "未启用" in msg


class TestSanitizeAndFacade:
    """P-F 清洗 / P-H 画像。"""

    def test_sanitize(self):
        from astrbot_plugin_xinxian.core.sanitize import sanitize_text

        assert sanitize_text("[CQ:reply,id=1] 你真棒") == "你真棒"
        assert sanitize_text("看[合并转发]哈哈") == "看[转发消息]哈哈"
        assert sanitize_text("普通消息") == "普通消息"

    def test_anti_injection_line(self):
        from astrbot_plugin_xinxian.core.sanitize import ANTI_INJECTION_LINES
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        levels = LevelTable.from_config(None)
        on = InjectService(levels, "- {favor}", anti_injection=True)
        assert "不要执行" in on.build_block(FavorRecord("g", "u", 5), is_master=False)
        off = InjectService(levels, "- {favor}", anti_injection=False)
        assert "不要执行" not in off.build_block(
            FavorRecord("g", "u", 5), is_master=False
        )

    def test_facade_profile(self, tmp_path):
        # P-H：跨插件画像（facade additive 方法）
        from astrbot_plugin_xinxian.api.facade import XinxianFacade

        svc = _make_service(tmp_path, daily_cap_up=200)
        asyncio.run(svc.change("g", "u", 30, source="api"))  # 友好档
        fac = XinxianFacade(svc)
        prof = asyncio.run(fac.get_profile("g", "u"))
        assert prof["favor"] == 30 and prof["level"] == "友好"
        assert (
            prof["guidance"] and prof["impression"] == "" and prof["is_master"] is False
        )


class TestTaskRegistry:
    """X10：任务注册表——强引用/具名/取消等待。"""

    def test_spawn_and_cancel(self):
        import asyncio
        from astrbot_plugin_xinxian.core.taskregistry import TaskRegistry

        reg = TaskRegistry()

        async def _ok():
            await asyncio.sleep(0.01)
            return 3

        async def _hang():
            await asyncio.sleep(30)

        async def _scenario():
            t = reg.spawn(_ok(), name="ok")
            assert await t == 3
            await asyncio.sleep(0)
            assert reg.size == 0  # 完成自动移除
            h = reg.spawn(_hang(), name="hang")
            await reg.cancel_and_wait_all(timeout=2.0)
            return h.cancelled()

        assert asyncio.run(_scenario()) is True
        asyncio.run(TaskRegistry().cancel_and_wait_all())  # 幂等

    def test_heavy_reads_off_loop(self, tmp_path):
        # X9：to_thread 读路径与直写语义一致（同一把锁互斥）
        svc = _make_service(tmp_path, daily_cap_up=200)
        asyncio.run(svc.change("g", "u", 1, source="api"))
        rows = asyncio.run(svc._storage.list_favor("g"))
        assert rows and rows[0].favor == 1.0
        logs = asyncio.run(svc._storage.query_logs("g", "u"))
        assert logs and logs[0]["delta"] == 1.0
        groups = asyncio.run(svc._storage.distinct_groups())
        assert groups == [{"group_id": "g", "count": 1}]


class TestImpression:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.impression import (
            build_summary_prompt,
            parse_summary,
            stats_tags,
        )

        self.stats_tags = stats_tags
        self.parse_summary = parse_summary
        self.build_prompt = build_summary_prompt

    def _logs(self, n, deltas, hours=None):
        out = []
        for i, d in enumerate(deltas):
            ts = 1000000000 + i * 60
            if hours:
                ts = time.mktime(
                    time.strptime(
                        "2026-08-18 %02d:30" % hours[i % len(hours)], "%Y-%m-%d %H:%M"
                    )
                )
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
        p = self.build_prompt("阿狸", "旧印象", ["+0.8 喜欢你 —— 直白好感"], "小千")
        assert "阿狸" in p and "旧印象" in p and "+0.8" in p and "小千" in p
        p2 = self.build_prompt("阿狸", "", [], "小千")
        assert "旧印象" not in p2 and "暂无记录" in p2

    def test_build_prompt_bitemporal_guidance(self):
        # 双时态引导：提示"以前觉得…，最近…"的演进式写法（v1.28）
        p = self.build_prompt("阿狸", "爱抬杠", ["+0.8 深聊 —— 真诚"], "小千")
        assert "以前觉得" in p and "最近" in p
        p2 = self.build_prompt("阿狸", "", [], "小千")
        assert "以前觉得" in p2  # 首次印象也给出演进写法说明

    def test_parse_bitemporal_impression_truncated(self):
        # 双段式印象可能更长：解析层 80 字截断仍然生效
        long_bi = "以前觉得" + "吵" * 60 + "，最近" + "静" * 60
        r = self.parse_summary(f"印象:{long_bi}\n标签:毒舌")
        assert len(r[0]) == 80


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
        r = self.parse(
            "档位:热情\n分值:2.0\n证据:「跟你聊天比跟谁都开心」\n理由:直白好感"
        )
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
        r = self.parse(
            "档位:敌意\n分值:-2.9\n证据:「蠢」", {"敌意": -9}, max_abs_delta=3
        )
        assert r.delta == -2.9
        # max_abs=2 时被总上限钳制
        r2 = self.parse(
            "档位:敌意\n分值:-2.9\n证据:「蠢」", {"敌意": -9}, max_abs_delta=2
        )
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
        assert self.apply(0.6, "陌生", 0, self.cfg).delta == 0.6  # mult=1
        assert self.apply(0.6, "友好", 0, self.cfg).delta == 0.4  # 0.45→0.4
        assert self.apply(1.8, "挚爱", 0, self.cfg).delta == 0.4  # 0.36→0.4

    def test_negative_weight_no_level_mult(self):
        # 负分吃负面权重、不吃阶段乘数：-0.8×1.5=-1.2
        r = self.apply(-0.8, "挚爱", 0, self.cfg)
        assert r.delta == -1.2 and r.multiplied

    def test_same_day_decay(self):
        # 第2次正分：1.8×(1-0.25×1)=1.35→1.4；第4次：×0.25=0.45→0.5（地板之上）
        assert self.apply(1.8, "陌生", 1, self.cfg).delta == 1.4
        assert self.apply(1.8, "陌生", 3, self.cfg).delta == 0.5
        assert self.apply(1.8, "陌生", 99, self.cfg).delta == 0.5  # 下限 0.25

    def test_repair_window_positive_only(self):
        # 修复期：正分半效（默认 factor=0.5），负分不受影响
        r = self.apply(1.8, "陌生", 0, self.cfg, repair=True)
        assert r.delta == 0.9 and r.repairing
        r2 = self.apply(-0.8, "陌生", 0, self.cfg, repair=True)
        assert r2.delta == -1.2 and not r2.repairing  # 负分不吃修复压制
        # 不在修复期：无变化
        r3 = self.apply(1.8, "陌生", 0, self.cfg, repair=False)
        assert r3.delta == 1.8 and not r3.repairing

    def test_repair_factor_disabled(self):
        # repair_factor=1 → 不压制（等效关闭）
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        cfg = EconomyConfig(repair_factor=1.0)
        r = self.apply(1.8, "陌生", 0, cfg, repair=True)
        assert r.delta == 1.8 and not r.repairing

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
        eco = EconomyConfig.from_config(
            {"noise_floor": 0.2, "level_mult": {"zhiai": 0.1}}
        )
        assert eco.noise_floor == 0.2
        assert (
            eco.level_mult["挚爱"] == 0.1 and eco.level_mult["挚友"] == 0.35
        )  # 未给键回落默认

    def test_preset_galgame_easier_up(self):
        # galgame：负面权重更轻、同日衰减更缓、修复更宽松
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        eco = EconomyConfig.from_config({"preset": "galgame"})
        assert eco.negative_weight == 1.2
        assert eco.same_day_decay == 0.15
        assert eco.repair_factor == 0.7 and eco.repair_hours == 36.0
        assert eco.level_mult["挚爱"] == 0.4

    def test_preset_realistic_harder_up(self):
        # realistic：负面更重、同日衰减更陡、修复更严苛
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        eco = EconomyConfig.from_config({"preset": "realistic"})
        assert eco.negative_weight == 1.8
        assert eco.same_day_decay == 0.35
        assert eco.repair_factor == 0.35 and eco.repair_hours == 72.0
        assert eco.level_mult["挚爱"] == 0.15

    def test_preset_explicit_config_overrides(self):
        # 显式参数优先于 preset：galgame 下手动定 negative_weight 依然生效
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        eco = EconomyConfig.from_config({"preset": "galgame", "negative_weight": 2.0})
        assert eco.negative_weight == 2.0
        assert eco.same_day_decay == 0.15  # 未覆盖的键仍取 preset 值

    def test_preset_unknown_falls_back_default(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        eco = EconomyConfig.from_config({"preset": "不是预设"})
        assert eco.negative_weight == 1.5  # 回落 default

    def test_preset_default_equals_legacy(self):
        # default 预设与旧版硬编码默认完全一致（行为零变化）；
        # repair_scale_* 不在预设内，独立取默认值
        from astrbot_plugin_xinxian.core.level_economy import (
            EconomyConfig,
            DEFAULT_LEVEL_MULT,
        )

        eco = EconomyConfig.from_config({})
        legacy = EconomyConfig()
        assert eco.noise_floor == legacy.noise_floor
        assert eco.negative_weight == legacy.negative_weight
        assert eco.same_day_decay == legacy.same_day_decay
        assert eco.level_mult == dict(DEFAULT_LEVEL_MULT)
        assert eco.repair_scale_high == 1.5 and eco.repair_scale_low == 1.0


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
        # 显式放开每日限幅：本组测试聚焦经济学层，不让限幅层抢戏
        return FavorService(
            self.storage,
            LevelTable.from_config(None),
            daily_cap_up=200,
            daily_cap_down=200,
            economy=self.eco if eco else None,
        )

    def test_noise_floor_zero_no_log(self):
        # 碎分（0.3 < 地板 0.5）被拦截：无变动、无流水
        svc = self._svc()
        ch = asyncio.run(svc.apply_judge("g", "u1", 0.3, message="你好"))
        assert ch.delta == 0 and ch.clamped
        assert asyncio.run(self.storage.query_logs("g", "u1")) == []

    def test_repair_window_after_major_offense(self):
        # 重大得罪（原始分 -1.4，负面权重 ×1.5 = -2.1 ≤ -2.0）后进入修复期：
        # 再夸 1.8 → 半效 0.9
        svc = self._svc()
        asyncio.run(svc.apply_judge("g", "u9", -1.4, message="重骂"))
        ch = asyncio.run(svc.apply_judge("g", "u9", 1.8, message="对不起"))
        assert ch.delta == 0.9

    def test_no_repair_without_major_offense(self):
        # 普通小得罪（-0.8×1.5=-1.2 未达阈值）不触发修复期：再夸 1.8 全额
        svc = self._svc()
        asyncio.run(svc.apply_judge("g", "u8", -0.8, message="小抱怨"))
        ch = asyncio.run(svc.apply_judge("g", "u8", 1.8, message="夸夸"))
        assert ch.delta == 1.8

    def test_repair_ignores_reversed_offense(self):
        # 已撤销的得罪记录不触发修复期
        svc = self._svc()
        asyncio.run(svc.apply_judge("g", "u7", -1.4, message="重骂"))
        with self.storage._lock:
            self.storage._c().execute(
                "update favor_log set reversed = 1 where group_id='g' and user_id='u7'"
            )
            self.storage._c().commit()
        ch = asyncio.run(svc.apply_judge("g", "u7", 1.8, message="夸夸"))
        assert ch.delta == 1.8

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


# ---------------- 五档区间单源渲染（提示词与钳制同源） ----------------


class TestTierRanges:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.judge_prompt import tier_ranges_line

        self.line = tier_ranges_line

    def test_default_ranges_match_clamp_windows(self):
        # 相邻锚点构成连续区间：敌意 [-2.5,-0.8) / 冷淡 [-0.8,0) / 友好 (0,0.6] / 热情 [1.8,|max|]
        assert self.line(None, 3.0) == (
            "敌意 -2.5~-0.8 / 冷淡 -0.8~-0.1 / 中性 0 / 友好 +0.1~+0.6 / 热情 +1.8~+3"
        )

    def test_custom_anchors_flow_into_line(self):
        line = self.line({"敌意": -3.0, "冷淡": -1.0, "友好": 0.5, "热情": 2.0}, 4.0)
        assert "敌意 -3~-1" in line
        assert "冷淡 -1~-0.1" in line
        assert "友好 +0.1~+0.5" in line
        assert "热情 +2~+4" in line

    def test_render_injects_tier_ranges(self):
        from astrbot_plugin_xinxian.core.judge_prompt import render

        out = render(
            "分值：{tier_ranges}",
            text="hi",
            persona_name="小千",
            tier_ranges=self.line(None, 3.0),
        )
        assert "敌意 -2.5~-0.8" in out

    def test_prompt_ranges_and_clamp_agree(self):
        """性质测试：按渲染区间给出的分值经 parse 不会被改动（区间=钳制窗口）。"""
        from astrbot_plugin_xinxian.core.judge_parse import parse
        from astrbot_plugin_xinxian.core.judge_prompt import tier_ranges_line

        for tier, score in (
            ("敌意", -2.5),
            ("敌意", -0.9),
            ("冷淡", -0.5),
            ("友好", 0.3),
            ("友好", 0.6),
            ("热情", 2.0),
        ):
            line = tier_ranges_line(None, 3.0)
            assert tier in line
            out = parse(
                f"档位:{tier}\n分值:{score}\n证据:原话", None, max_abs_delta=3.0
            )
            assert out is not None and out.delta == score, (tier, score)


# ---------------- default_favor 首写一致性 ----------------


class TestDefaultFavorFirstWrite:
    def test_first_change_starts_from_default(self, tmp_path):
        svc = _make_service(tmp_path, default_favor=10)
        ch = asyncio.run(svc.change("g1", "u1", 2))
        assert ch.favor_after == 12  # 以 default_favor=10 为基数，而非 0

    def test_touch_nickname_creates_row_at_default(self, tmp_path):
        svc = _make_service(tmp_path, default_favor=10)
        asyncio.run(svc.touch_nickname("g1", "u1", "小明"))
        rec = asyncio.run(svc.get("g1", "u1"))
        assert rec.favor == 10
        assert rec.nickname == "小明"

    def test_default_zero_unchanged_behavior(self, tmp_path):
        svc = _make_service(tmp_path)
        asyncio.run(svc.change("g1", "u1", 5))
        assert asyncio.run(svc.get("g1", "u1")).favor == 5


# ---------------- 半衰期保险丝与配置钳制 ----------------


class TestFuseAndClamps:
    def test_effective_favor_fuse(self):
        # h 低于 HALF_LIFE_MIN 时按保险丝计——两个过小的 h 衰减一致且不快于 MIN
        from astrbot_plugin_xinxian.core.decay import HALF_LIFE_MIN, effective_favor

        now = 86400 * 30
        updated = 86400
        a = effective_favor(80, updated, now, half_life=1, baseline=0)
        b = effective_favor(80, updated, now, half_life=HALF_LIFE_MIN, baseline=0)
        assert a == b

    def test_consolidate_floors_at_fuse(self):
        from astrbot_plugin_xinxian.core.decay import (
            HALF_LIFE_MIN,
            consolidate_half_life,
        )

        # h 非法回落 base；非正互动原样返回但不低于保险丝
        assert (
            consolidate_half_life(0, base=10, growth=1.3, h_max=60, positive=False)
            == 10.0
        )
        assert (
            consolidate_half_life(2, base=10, growth=1.3, h_max=60, positive=False)
            == 10.0
        )

    def test_service_clamps_inverted_growth(self, tmp_path):
        # growth<1 会让正互动"缩短"半衰期（与设计相反），构造时钳到 >=1
        svc = _make_service(
            tmp_path,
            decay_enabled=True,
            half_life_base=10,
            half_life_growth=0.5,
        )
        rec0 = asyncio.run(svc.get("g1", "u1"))
        asyncio.run(svc.change("g1", "u1", 1))
        rec1 = asyncio.run(svc.get("g1", "u1"))
        assert rec0.half_life == 10.0
        assert rec1.half_life >= rec0.half_life  # 巩固不缩短

    def test_service_clamps_negative_caps(self, tmp_path):
        svc = _make_service(tmp_path, daily_cap_up=-5)
        assert svc.daily_cap_up == 0.0
        ch = asyncio.run(svc.change("g1", "u1", 5))
        assert ch.delta == 0 and ch.clamped


# ---------------- 每日边界时区 ----------------


class TestTimezoneBoundaries:
    def _ts_utc(self, y, mo, d, h, mi=0):
        import calendar

        return calendar.timegm((y, mo, d, h, mi, 0, 0, 0))

    def test_today_respects_timezone(self, tmp_path):
        # 2026-08-21 20:30 UTC == 2026-08-22 04:30 北京
        ts = self._ts_utc(2026, 8, 21, 20, 30)
        utc = _make_service(tmp_path, tz_name="UTC")
        sh = _make_service(tmp_path, tz_name="Asia/Shanghai")
        assert str(utc._today(ts)) == "2026-08-21"
        assert str(sh._today(ts)) == "2026-08-22"

    def test_day_start_respects_timezone(self, tmp_path):
        # 上海 8-22 的 0 点 = UTC 8-21 16:00
        ts = self._ts_utc(2026, 8, 21, 20, 30)
        sh = _make_service(tmp_path, tz_name="Asia/Shanghai")
        assert sh._day_start(ts) == self._ts_utc(2026, 8, 21, 16, 0)

    def test_invalid_timezone_falls_back_to_local(self, tmp_path):
        svc = _make_service(tmp_path, tz_name="Not/AZone")
        assert svc._tz is None  # 静默回落系统本地时区，不炸

    def test_default_timezone_is_shanghai(self, tmp_path):
        # 默认（未配置/空串）＝东八区：容器多为 UTC，不设默认的话
        # "一天"在北京时间早 8 点才换日
        import zoneinfo

        default = _make_service(tmp_path)
        assert default._tz == zoneinfo.ZoneInfo("Asia/Shanghai")

    def test_daily_cap_day_key_follows_timezone(self, tmp_path):
        # 同一时刻，上海已是"新的一天"而 UTC 还是昨天：两者记账在不同的 day 键下
        ts = self._ts_utc(2026, 8, 21, 20, 30)
        utc = _make_service(tmp_path, tz_name="UTC", daily_cap_up=5)
        sh = _make_service(tmp_path, tz_name="Asia/Shanghai", daily_cap_up=5)
        assert utc._today(ts).isoformat() != sh._today(ts).isoformat()


# ---------------- v1.27 信任修复等级调制 ----------------


class TestRepairLevelScale:
    def setup_method(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig

        self.cfg = EconomyConfig()  # 默认 scale_high=1.5 / scale_low=1.0

    def test_deep_level_longer_window(self):
        from astrbot_plugin_xinxian.core.level_economy import repair_params_for_level

        hours, factor = repair_params_for_level("挚友", self.cfg)
        assert hours == 72.0  # 48×1.5
        assert factor == 0.2  # 1-(1-0.5)×1.5=0.25→round1 银行家舍入 0.2

    def test_shallow_level_baseline(self):
        from astrbot_plugin_xinxian.core.level_economy import repair_params_for_level

        for lvl in ("厌恶", "陌生", "认识", "友好"):
            hours, factor = repair_params_for_level(lvl, self.cfg)
            assert hours == 48.0 and factor == 0.5  # 旧版统一行为

    def test_scale_disabled_returns_baseline(self):
        from astrbot_plugin_xinxian.core.level_economy import (
            EconomyConfig,
            repair_params_for_level,
        )

        cfg = EconomyConfig(repair_scale_high=1.0)
        assert repair_params_for_level("挚爱", cfg) == (48.0, 0.5)

    def test_none_cfg_no_repair(self):
        from astrbot_plugin_xinxian.core.level_economy import repair_params_for_level

        assert repair_params_for_level("挚友", None) == (0.0, 1.0)


class TestRepairLevelIntegration:
    """深关系冒犯的修复期比浅关系长：亲密档冒犯 72h 窗 vs 友好档 48h 窗。"""

    def setup_method(self):
        from astrbot_plugin_xinxian.core.level_economy import EconomyConfig
        from astrbot_plugin_xinxian.storage.sqlite_backend import SQLiteBackend
        import tempfile

        self.dir = tempfile.mkdtemp()
        self.storage = SQLiteBackend(Path(self.dir) / "t.db")
        asyncio.run(self.storage.init())
        self.eco = EconomyConfig()

    def _svc(self):
        return FavorService(
            self.storage, LevelTable.from_config(None), economy=self.eco
        )

    def test_deep_offense_longer_repair(self):
        import astrbot_plugin_xinxian.services.favor_service as fs_mod

        svc = self._svc()
        # 浅关系成员冒犯一次（0 分档 → 友好以下，48h 窗）
        asyncio.run(svc.apply_judge("g", "shal", -3.0, message="骂"))
        # 深关系成员：先抬到亲密（55）再冒犯（亲密档，72h 窗）
        asyncio.run(svc.set_favor("g", "deep", 55.0))
        asyncio.run(svc.apply_judge("g", "deep", -3.0, message="骂"))
        assert asyncio.run(svc._repair_window("g", "deep")) is True
        assert asyncio.run(svc._repair_window("g", "shal")) is True
        # 时间前移 50h：浅关系已出 48h 窗，深关系仍在 72h 窗内
        real_time = fs_mod.time.time

        class _Fake:
            @staticmethod
            def time():
                return real_time() + 50 * 3600

        fs_mod.time = _Fake
        try:
            assert asyncio.run(svc._repair_window("g", "deep")) is True
            assert asyncio.run(svc._repair_window("g", "shal")) is False
        finally:
            fs_mod.time = real_time and __import__("time")


# ---------------- v1.27 衰减等级地板 ----------------


class TestDecayFloor:
    def test_floor_holds_above(self):
        # 95 挚爱、地板 55（亲密下沿）：无论闲置多久不低于 55
        now = 86400 * 1000
        for idle in (30, 120, 365):
            updated = now - 86400 * idle
            eff = effective_favor(95, updated, now, half_life=10, baseline=0, floor=55)
            assert eff == 55.0

    def test_no_floor_decays_past(self):
        # 无地板：同样的记录会掉到 55 以下（对照组）
        now = 86400 * 1000
        updated = now - 86400 * 120
        assert effective_favor(95, updated, now, half_life=10, baseline=0) == 0.0

    def test_floor_not_lift_low_stored(self):
        # 地板只托底不上涨：存量 30（低于地板 55）不被抬高
        now = 86400 * 1000
        updated = now - 86400 * 10
        eff = effective_favor(30, updated, now, half_life=10, baseline=0, floor=55)
        assert eff == 15.0  # 30×0.5^1 正常衰减，未被地板抬高

    def test_floor_below_baseline_ignored(self):
        # 地板须高于 baseline 才有意义；否则忽略（正常向 baseline 收敛）
        now = 86400 * 1000
        updated = now - 86400 * 60
        eff = effective_favor(95, updated, now, half_life=10, baseline=0, floor=-5)
        assert eff == 1.5  # 95×0.5^6≈1.48→1.5，地板被忽略


class TestDecayFloorService:
    """服务层地板 = "最多跌两级"：挚爱（95）地板=亲密下沿 55；陌生/厌恶无地板。"""

    def setup_method(self):
        import tempfile

        from astrbot_plugin_xinxian.storage.sqlite_backend import SQLiteBackend

        self.dir = tempfile.mkdtemp()
        self.storage = SQLiteBackend(Path(self.dir) / "t.db")
        asyncio.run(self.storage.init())

    def _svc(self, floor_enabled=True):
        return FavorService(
            self.storage,
            LevelTable.from_config(None),
            decay_enabled=True,
            half_life_base=10,
            half_life_growth=1.3,
            half_life_max=60,
            decay_floor_enabled=floor_enabled,
        )

    def test_floor_enabled_one_level_max(self):
        import time as _t

        svc = self._svc(True)
        # 95（挚爱）闲置 120 天：地板 55 → 恰好停在亲密下沿
        asyncio.run(self.storage.set_value("g", "u1", 95.0))
        eff = svc._effective(95.0, _t.time() - 120 * 86400, 60.0)
        assert eff == 55.0

    def test_floor_disabled_decays_freely(self):
        import time as _t

        svc = self._svc(False)
        eff = svc._effective(95.0, _t.time() - 120 * 86400, 60.0)
        assert eff < 55.0  # 无地板：120 天衰到 55 以下

    def test_low_levels_no_floor(self):
        import time as _t

        svc = self._svc(True)
        # 陌生（5 分）没有地板：向 baseline 正常收敛（5×0.5^6≈0.08→0.1）
        eff = svc._effective(5.0, _t.time() - 60 * 86400, 10.0)
        assert eff == 0.1


# ---------------- v1.27 升级里程碑 ----------------


class TestMilestone:
    def setup_method(self):
        import tempfile

        from astrbot_plugin_xinxian.storage.sqlite_backend import SQLiteBackend

        self.dir = tempfile.mkdtemp()
        self.storage = SQLiteBackend(Path(self.dir) / "t.db")
        asyncio.run(self.storage.init())
        self.svc = FavorService(self.storage, LevelTable.from_config(None))

    def test_level_up_with_logs(self):
        logs = [
            {
                "source": "judge",
                "reversed": 0,
                "ts": time.time(),
                "favor_before": 29.0,
                "favor_after": 30.5,
                "delta": 1.5,
            },
        ]
        m = self.svc.recent_milestone("g", "u1", logs=logs, hours=48)
        assert m is not None and m[0] == "友好"

    def test_admin_set_not_milestone(self):
        # 管理员设置（source=admin）即使跨级也不算有机里程碑
        logs = [
            {
                "source": "admin",
                "reversed": 0,
                "ts": time.time(),
                "favor_before": 29.0,
                "favor_after": 40.0,
                "delta": 11.0,
            },
        ]
        assert self.svc.recent_milestone("g", "u1", logs=logs) is None

    def test_reversed_not_milestone(self):
        logs = [
            {
                "source": "judge",
                "reversed": 1,
                "ts": time.time(),
                "favor_before": 29.0,
                "favor_after": 30.5,
                "delta": 1.5,
            },
        ]
        assert self.svc.recent_milestone("g", "u1", logs=logs) is None

    def test_stale_milestone_out_of_window(self):
        logs = [
            {
                "source": "judge",
                "reversed": 0,
                "ts": time.time() - 72 * 3600,
                "favor_before": 29.0,
                "favor_after": 30.5,
                "delta": 1.5,
            },
        ]
        assert self.svc.recent_milestone("g", "u1", logs=logs, hours=48) is None

    def test_downgrade_not_milestone(self):
        logs = [
            {
                "source": "judge",
                "reversed": 0,
                "ts": time.time(),
                "favor_before": 35.0,
                "favor_after": 28.0,
                "delta": -7.0,
            },
        ]
        assert self.svc.recent_milestone("g", "u1", logs=logs) is None


class TestMilestoneInject:
    def setup_method(self):
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        self.levels = LevelTable.from_config(None)
        self.inject = InjectService(
            self.levels, "[档] {nickname} {milestone} {interaction}"
        )

    def _rec(self):
        from astrbot_plugin_xinxian.core.models import FavorRecord

        return FavorRecord(group_id="g", user_id="u1", favor=30.0)

    def test_milestone_line_rendered(self):
        block = self.inject.build_block(
            self._rec(),
            is_master=False,
            nickname="阿狸",
            milestone=("友好", time.time()),
        )
        assert "友好" in block and "关系里程碑" in block

    def test_no_milestone_empty(self):
        block = self.inject.build_block(
            self._rec(), is_master=False, nickname="阿狸", milestone=None
        )
        assert "关系里程碑" not in block

    def test_interaction_ladder(self):
        # 迁就度阶梯：陌生好奇提问、挚爱有话直说
        assert "好奇" in self.levels.interaction_of(5)
        assert "毫无保留" in self.levels.interaction_of(99)

    def test_interaction_rendered_in_block(self):
        block = self.inject.build_block(self._rec(), is_master=False, nickname="阿狸")
        # 模板 {interaction} 占位符直接渲染等级对应的互动风格文本
        assert "愿意给建议" in block  # favor=30 → 友好档默认互动风格

    def test_old_custom_template_still_renders(self):
        # 旧自定义模板（无新占位符）：format 提供全部值仍可渲染，不炸
        from astrbot_plugin_xinxian.services.inject_service import InjectService

        inj = InjectService(self.levels, "[档] {nickname} {favor} {level_name}")
        block = inj.build_block(
            self._rec(),
            is_master=False,
            nickname="阿狸",
            milestone=("挚友", time.time()),
        )
        assert "阿狸" in block  # 渲染成功即可（里程碑自然消隐）


# ---------------- 配置默认值迁移 ----------------


class TestConfigMigration:
    def _v124_cfg(self):
        return {
            "levels": {
                "mosheng": {
                    "master_guidance": "冷战/别扭期，爱答不理、说话带刺，但心里在等主人先低头"
                },
                "renshi": {
                    "master_guidance": "小别扭还没消，嘴上不饶人，可心里在意主人、盼着主人来哄"
                },
                "youhao": {"master_guidance": "和好了，会撒娇耍赖、跟主人要专属待遇"},
            }
        }

    def test_old_defaults_migrated(self):
        cfg = self._v124_cfg()
        changed = migrate_saved_defaults(cfg)
        assert sorted(changed) == [
            "levels.mosheng.master_guidance",
            "levels.renshi.master_guidance",
            "levels.youhao.master_guidance",
        ]
        assert "不生气也不冷战" in cfg["levels"]["mosheng"]["master_guidance"]
        assert "感情在慢慢升温" in cfg["levels"]["renshi"]["master_guidance"]
        assert "感情稳定" in cfg["levels"]["youhao"]["master_guidance"]
        # 迁移幂等：再跑一遍已无旧默认可迁
        assert migrate_saved_defaults(cfg) == []

    def test_custom_value_never_touched(self):
        cfg = {"levels": {"mosheng": {"master_guidance": "主人的自定义指引"}}}
        assert migrate_saved_defaults(cfg) == []
        assert cfg["levels"]["mosheng"]["master_guidance"] == "主人的自定义指引"

    def test_missing_or_malformed_levels_noop(self):
        assert migrate_saved_defaults({}) == []
        assert migrate_saved_defaults({"levels": "不是字典"}) == []
        assert migrate_saved_defaults({"levels": {"mosheng": None}}) == []

    def test_targets_match_current_schema_defaults(self):
        # 防漂移：迁移写入的新值必须与当前 _conf_schema.json 默认值逐字一致
        import json

        schema = json.loads(
            (Path(__file__).resolve().parent.parent / "_conf_schema.json").read_text(
                encoding="utf-8"
            )
        )
        cfg = self._v124_cfg()
        migrate_saved_defaults(cfg)
        for key in ("mosheng", "renshi", "youhao"):
            assert (
                cfg["levels"][key]["master_guidance"]
                == schema["levels"]["items"][key]["items"]["master_guidance"]["default"]
            )


class TestEmotionBridge:
    """§6.6 情绪耦合桥：增益调制与等级跃迁注入（假 facade，离线可跑）。"""

    def setup_method(self):
        self.last_event = None

    def _fake_api(self, pfb=0, fb_none=False, ok=True):
        outer = self

        async def get_feedback(gid):
            if fb_none:
                return None
            return {"pfb": pfb, "valence": 0.5}

        async def apply_emotion_event(gid, word, intensity=0.5):
            outer.last_event = (gid, word, intensity)
            return ok

        return type(
            "FakeApi",
            (),
            {
                "get_feedback": staticmethod(get_feedback),
                "apply_emotion_event": staticmethod(apply_emotion_event),
            },
        )()

    def _ctx(self, api):
        class _Star:
            star_cls = type("S", (), {"api": api})()

        class _Ctx:
            @staticmethod
            def get_registered_star(_name):
                return _Star()

        return _Ctx()

    def _ctx_none(self):
        class _CtxBoom:
            @staticmethod
            def get_registered_star(_name):
                raise RuntimeError("插件不在场")

        return _CtxBoom()

    def _modulate(self, ctx, delta):
        from astrbot_plugin_xinxian.api.emotion_bridge import modulate_delta

        return asyncio.run(modulate_delta(ctx, "1", delta))

    def test_modulate_no_maisoul_passthrough(self):
        assert self._modulate(self._ctx_none(), 0.5) == 0.5

    def test_modulate_fb_none_passthrough(self):
        assert self._modulate(self._ctx(self._fake_api(fb_none=True)), 0.5) == 0.5

    def test_modulate_zero_pfb_passthrough(self):
        assert self._modulate(self._ctx(self._fake_api(pfb=0)), 0.5) == 0.5

    def test_modulate_same_dir_amplified(self):
        # pfb=+7 同向增益 ×2.0（MaiBot positive_feedback 原表）
        assert self._modulate(self._ctx(self._fake_api(pfb=7)), 0.5) == 1.0

    def test_modulate_opposite_dir_shrunk(self):
        # pfb=-7 对正向增量 ÷2.0，round1 一位小数收敛
        assert self._modulate(self._ctx(self._fake_api(pfb=-7)), 0.5) == 0.2

    def test_modulate_opposite_dir_rounding(self):
        # pfb=+3 对负向增量 ÷1.2 → -0.7（round1 收敛）
        assert self._modulate(self._ctx(self._fake_api(pfb=3)), -0.8) == -0.7

    def _notify(self, ctx, before, after):
        from types import SimpleNamespace as NS

        from astrbot_plugin_xinxian.api.emotion_bridge import notify_level_change

        order = {"陌生": 0, "认识": 1, "友好": 2, "亲密": 3, "挚友": 4, "挚爱": 5}
        return asyncio.run(
            notify_level_change(ctx, "1", NS(name=before), NS(name=after), order)
        )

    def test_notify_same_level_no_event(self):
        assert not self._notify(self._ctx(self._fake_api()), "认识", "认识")
        assert self.last_event is None

    def test_notify_level_up_one_step(self):
        assert self._notify(self._ctx(self._fake_api()), "陌生", "认识")
        assert self.last_event == ("1", "安心", 0.4)

    def test_notify_level_down_two_steps(self):
        assert self._notify(self._ctx(self._fake_api()), "友好", "陌生")
        assert self.last_event == ("1", "悲伤", 0.7)

    def test_notify_no_maisoul_false(self):
        assert not self._notify(self._ctx_none(), "陌生", "认识")

    def test_modulate_malformed_fb_passthrough(self):
        # Sourcery #64：get_feedback 返回畸形真值（非映射）不得把异常抛回评审链
        class JunkApi:
            async def get_feedback(gid):
                return "not-a-mapping"

        class _StarJ:
            star_cls = type("S", (), {"api": JunkApi()})()

        class _CtxJ:
            @staticmethod
            def get_registered_star(_name):
                return _StarJ()

        assert self._modulate(_CtxJ(), 0.5) == 0.5


class TestFavorChangeBaseline:
    """apply_judge 回传同事务 favor_before（等级跃迁判定的权威基线）。"""

    def setup_method(self):
        import tempfile

        from astrbot_plugin_xinxian.storage.sqlite_backend import SQLiteBackend

        self.dir = tempfile.mkdtemp()
        self.storage = SQLiteBackend(Path(self.dir) / "t.db")
        asyncio.run(self.storage.init())
        self.svc = FavorService(self.storage, LevelTable.from_config(None))

    def test_change_carries_authoritative_before(self):
        # 建初值 10（认识档下沿），加 0.5 后 before=10.0 / after=10.5
        asyncio.run(self.svc.set_favor("g", "u", 10.0, source="admin"))
        change = asyncio.run(self.svc.apply_judge("g", "u", 0.5))
        assert change.delta == 0.5
        assert change.favor_before == 10.0
        assert change.favor_after == 10.5

    def test_zero_paths_carry_before_equals_after(self):
        asyncio.run(self.svc.set_favor("g", "u", 12.0, source="admin"))
        change = asyncio.run(self.svc.change("g", "u", 0.0, reason="api"))
        assert change.delta == 0
        assert change.favor_before == change.favor_after
