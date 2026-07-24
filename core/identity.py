"""心弦好感度 - 身份判定。

主人身份只认 QQ 号，不认昵称（昵称谁都能改，QQ 号才是稳定标识）。
"""

from __future__ import annotations

from typing import Collection


def parse_master_ids(raw: str) -> list[str]:
    """解析配置里的主人 QQ 号（英文/中文逗号分隔）。"""
    return [
        x.strip()
        for x in str(raw or "").replace("，", ",").split(",")
        if x.strip()
    ]


def is_master(user_id: str, master_ids: Collection[str]) -> bool:
    """判断 QQ 号是否为主人。"""
    return str(user_id) in {str(m).strip() for m in master_ids if str(m).strip()}
