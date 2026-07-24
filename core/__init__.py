"""心弦好感度 - 领域核心层。

纯 Python 逻辑，不依赖 AstrBot，可独立单元测试。
"""

from .decimal import fmt, round1
from .models import FavorChange, FavorRecord, LevelDef
from .levels import DEFAULT_LEVELS, LevelTable
from .events import EventRule, EventType, RuleMatcher
from .identity import is_master, parse_master_ids

__all__ = [
    "round1",
    "fmt",
    "FavorChange",
    "FavorRecord",
    "LevelDef",
    "DEFAULT_LEVELS",
    "LevelTable",
    "EventRule",
    "EventType",
    "RuleMatcher",
    "is_master",
    "parse_master_ids",
]
