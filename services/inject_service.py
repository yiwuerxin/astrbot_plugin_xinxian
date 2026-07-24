"""心弦好感度 - 提示词注入服务。

把"对方好感度档案"追加到 system_prompt，只追加、不覆盖，
与其他注入类插件（如 AstrNa）和平共处。
"""

from __future__ import annotations

from ..core.levels import LevelTable
from ..core.models import FavorRecord


class InjectService:
    """好感度档案注入服务。"""

    def __init__(
        self,
        levels: LevelTable,
        template: str,
        master_title: str = "主人",
        max_favor: int = 100,
    ) -> None:
        self._levels = levels
        self._template = template
        self._master_title = master_title
        self._max_favor = max_favor

    def build_block(
        self,
        record: FavorRecord,
        *,
        is_master: bool,
        nickname: str | None = None,
    ) -> str:
        """按模板渲染好感度档案块。主人身份以文本叠加，不影响数值逻辑。"""
        lv = self._levels.level_of(record.favor)
        master_line = (
            f"，TA 是你的{self._master_title}"
            "（最高亲密关系；好感度数值仍如实反映 TA 近期对你的态度）"
            if is_master
            else ""
        )
        return self._template.format(
            nickname=nickname or "对方",
            user_id=record.user_id,
            master_line=master_line,
            favor=record.favor,
            max_favor=self._max_favor,
            level_name=lv.name,
            level_guidance=lv.guidance,
        )

    def inject(self, req, block: str) -> None:
        """把档案块追加到 req.system_prompt（只追加不覆盖）。"""
        sp = getattr(req, "system_prompt", "") or ""
        req.system_prompt = f"{sp}\n\n{block}" if sp else block
