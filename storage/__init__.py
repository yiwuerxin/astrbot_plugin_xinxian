"""心弦好感度 - 持久化层。

StorageBackend 抽象接口 + SQLite 默认实现 + schema 迁移。
更换存储后端（JSON/Redis 等）只需新增实现类，上层无感。
"""

from .base import StorageBackend
from .sqlite_backend import SQLiteBackend

__all__ = ["StorageBackend", "SQLiteBackend"]
