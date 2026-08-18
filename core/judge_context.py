"""心弦好感度 - 会话历史文本提取。

AstrBot 4.26 的会话历史 content 有两种形态：
- 纯字符串（部分 user 消息）
- 结构化列表 [{type:'text'|'think'|..., text/...}]（assistant 回复与部分 user 消息）

评审上下文只取文本：列表形态拼接所有 type=='text' 部分的文字，
think/图片/工具调用等一律跳过。纯函数，可单测。
"""

from __future__ import annotations


def extract_history_text(content) -> str:
    """从历史消息 content 提取纯文本；无可取文本返回空串。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for seg in content:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") != "text":
                continue  # 跳过 think / 图片 / 工具调用等
            t = str(seg.get("text") or "").strip()
            if t:
                parts.append(t)
        return "\n".join(parts)
    return ""
