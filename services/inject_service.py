"""心弦好感度 - 提示词注入服务。

把"对方好感度档案"追加到 system_prompt，只追加、不覆盖，
与其他注入类插件（如 AstrNa）和平共处。
"""

from __future__ import annotations

import time

from ..core.decimal import fmt
from ..core.levels import LevelTable
from ..core.models import FavorRecord
from ..core.relationship import RelationshipTable


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
    ) -> None:
        self._levels = levels
        self._template = template
        self._master_title = master_title
        self._max_favor = max_favor
        self._relationships = relationships
        self._persona_anchor = persona_anchor

    def build_block(
        self,
        record: FavorRecord,
        *,
        is_master: bool,
        nickname: str | None = None,
        recent_events: list[dict] | None = None,
    ) -> str:
        """按模板渲染好感度档案块。主人身份以文本叠加，不影响数值逻辑。

        recent_events：最近变动流水（dict 列表），渲染为「近期印象」注入，
        让小千记得具体的事，而非只看一个分数。
        """
        lv = self._levels.level_of(record.favor)
        master_line = (
            f"，TA 是你的{self._master_title}"
            "（最高亲密关系；好感度数值仍如实反映 TA 近期对你的态度）"
            if is_master
            else ""
        )
        events_block = self._format_events(recent_events or [])
        relationship_block = self._format_relationship(record.relationship)
        block = self._template.format(
            nickname=nickname or "对方",
            user_id=record.user_id,
            master_line=master_line,
            favor=fmt(record.favor),
            max_favor=fmt(self._max_favor),
            level_name=lv.name,
            level_guidance=lv.guidance,
            recent_events=events_block,
            relationship=relationship_block,
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
