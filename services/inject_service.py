"""心弦好感度 - 提示词注入服务。

把"对方好感度档案"追加到 system_prompt，只追加、不覆盖，
与其他注入类插件（如 AstrNa）和平共处。
"""

from __future__ import annotations

import time
from string import Formatter

from ..core.decimal import fmt
from ..core.levels import LevelTable
from ..core.models import FavorRecord
from ..core.relationship import RelationshipTable


# 主人身份的默认注入提示：身份恒定 + 好感照常涨跌（不豁免）；具体语气
# 由主人版态度指引（levels.*.master_guidance）承接，身份行只声明语义切换。
# 用 {master_title} 占位称谓；inject.master_prompt 配置可覆盖整段。
DEFAULT_MASTER_PROMPT = (
    "，TA 是你的{master_title}。主人身份恒定，好感度照常涨跌、不豁免——"
    "下面的等级与态度指引按主人关系理解（负好感＝闹别扭，正值＝亲疏程度，不是对外人的语义）"
)

# 模板合法占位符全集（build_block 的 format 关键字参数）
_TEMPLATE_FIELDS = frozenset({
    "nickname", "user_id", "master_line", "favor", "max_favor", "level_name",
    "level_guidance", "disclosure", "interaction", "recent_events",
    "relationship", "impression", "milestone",
})


class InjectService:
    """好感度档案注入服务。"""

    def __init__(
        self,
        levels: LevelTable,
        template: str,
        master_title: str = "主人",
        max_favor: float = 100,
        relationships: RelationshipTable | None = None,
        persona_anchor: str = "",
        master_prompt: str = "",
    ) -> None:
        self._levels = levels
        self._template = template
        self._master_title = master_title
        self._max_favor = max_favor
        self._relationships = relationships
        self._persona_anchor = persona_anchor
        # 主人提示：留空用默认；用 replace 替换 {master_title}，避免用户自定义文本
        # 里其他花括号被 .format 误解析。
        self._master_tpl = (master_prompt or "").strip() or DEFAULT_MASTER_PROMPT

    @staticmethod
    def validate_template(template: str) -> str | None:
        """静态校验注入模板占位符（X2）；返回问题描述，None=合法。

        自定义模板里的未知花括号（如粘贴的 JSON 示例）会让 build_block 的
        format 在每次 LLM 请求上抛 KeyError——注入是每次对话的必经路径，
        必须在装配期发现并回落默认模板，而非运行期炸注入。位置参数 {}
        同样非法（format 只按关键字供参）；{{转义}} 合法。
        """
        try:
            fields = [f for _, f, _, _ in Formatter().parse(template or "")]
        except ValueError as e:
            return f"模板语法错误：{e}"
        unknown = [
            (f if f else "<位置参数>") for f in fields
            if f is not None and (f == "" or f not in _TEMPLATE_FIELDS)
        ]
        return f"未知占位符 {unknown}" if unknown else None

    @staticmethod
    def _format_impression(record: FavorRecord) -> str:
        """把成员印象+标签渲染为「TA 给你的印象」段；无印象返回空串（模板里自然消失）。"""
        imp = (record.impression or "").strip()
        if not imp:
            return ""
        tags = record.parsed_tags()
        tag_line = f"（标签：{'、'.join(tags)}）" if tags else ""
        return f"\n- TA 给你的印象：{imp}{tag_line}"

    def build_block(
        self,
        record: FavorRecord,
        *,
        is_master: bool,
        nickname: str | None = None,
        recent_events: list[dict] | None = None,
        milestone: tuple[str, float] | None = None,
    ) -> str:
        """按模板渲染好感度档案块。主人身份以文本叠加，不影响数值逻辑。

        recent_events：最近变动流水（dict 列表），渲染为「近期印象」注入，
        让小千记得具体的事，而非只看一个分数。
        milestone：(新等级名, ts)——48h 内的升级跨越，渲染为「关系里程碑」，
        让小千"知道"关系刚升温（LoveyDovey/原神式升级仪式感）；None 则空。
        """
        lv = self._levels.level_of(record.favor)
        master_line = (
            self._master_tpl.replace("{master_title}", self._master_title)
            if is_master
            else ""
        )
        guidance = self._levels.guidance_of(record.favor, master=is_master)
        disclosure = self._levels.disclosure_of(record.favor)
        interaction = self._levels.interaction_of(record.favor)
        events_block = self._format_events(recent_events or [])
        relationship_block = self._format_relationship(record.relationship)
        impression_block = self._format_impression(record)
        milestone_block = self._format_milestone(milestone)
        block = self._template.format(
            nickname=nickname or "对方",
            user_id=record.user_id,
            master_line=master_line,
            favor=fmt(record.favor),
            max_favor=fmt(self._max_favor),
            level_name=lv.name,
            level_guidance=guidance,
            disclosure=disclosure,
            interaction=interaction,
            recent_events=events_block,
            relationship=relationship_block,
            impression=impression_block,
            milestone=milestone_block,
        )
        if self._persona_anchor:
            block += "\n" + self._persona_anchor
        return block

    def _format_relationship(self, value: str) -> str:
        """把关系类型渲染为「你们的关系」段；未启用/未设置返回空串（模板里自然消失）。"""
        if self._relationships is None:
            return ""
        resolved = self._relationships.resolve(value)
        if not resolved:
            return ""
        label, guidance = resolved
        return f"\n- 你们的关系：{label}（{guidance}）"

    @staticmethod
    def _format_milestone(milestone: tuple[str, float] | None) -> str:
        """把升级里程碑渲染为「关系里程碑」段；无里程碑返回空串。"""
        if not milestone:
            return ""
        name, ts = milestone
        now = time.time()
        days = int((now - float(ts or 0)) // 86400)
        when = "今天" if days <= 0 else ("昨天" if days == 1 else f"{days}天前")
        return f"\n- 关系里程碑：{when}你们的关系刚升到了「{name}」——可以自然地提起这份更近的关系"

    @staticmethod
    def _format_events(events: list[dict]) -> str:
        """把最近变动渲染为「近期印象」段；无内容返回空串（模板里自然消失）。"""
        if not events:
            return ""
        now = time.time()
        lines: list[str] = []
        for e in events:
            reason = str(e.get("reason") or "").strip() or "变动"
            d = float(e.get("delta") or 0)
            sign = "+" if d > 0 else ""
            days = int((now - float(e.get("ts") or 0)) // 86400)
            when = "今天" if days <= 0 else ("昨天" if days == 1 else f"{days}天前")
            lines.append(f"  · {reason}（{sign}{fmt(d)}，{when}）")
        return "\n- 近期印象：\n" + "\n".join(lines)

    def inject(self, req, block: str) -> None:
        """把档案块追加到 req.system_prompt（只追加不覆盖）。"""
        sp = getattr(req, "system_prompt", "") or ""
        req.system_prompt = f"{sp}\n\n{block}" if sp else block
