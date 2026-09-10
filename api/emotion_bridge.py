"""§6.6 情绪-关系耦合（心弦侧桥，v1.31.0）。

与 astrbot_plugin_maisoul 的双向数值联动（只交换数值不渲染提示词，
P-H 教训对情绪面同样适用）：

- 方向①（麦麦情绪 → 好感增益）：评审出分后读麦麦 pfb（连续同向情绪
  累积，-7..+7），同向好感增量 × FEEDBACK_GAIN[|pfb|]（MaiBot
  positive_feedback 原参数，最高 ×2.0）、异向 ÷ 同表（最高 ÷2.0）——
  麦麦这段对话聊得开心，好感成长上调；连续不快则反向压制。
- 方向②（好感等级 → 麦麦情绪）：等级跃迁时向麦麦注入情绪事件
  （升级→安心/开心，降级→委屈/悲伤），强度按跨越档数；情绪随麦麦
  会话驻留并按其半衰期衰减，影响接下来几分钟的语气。

降级契约：麦麦未装 / facade 缺失 / 两侧任一开关关闭 / 会话不存在，
所有函数返回原值或 False，绝不抛异常——联动是增强不是依赖。
"""

from __future__ import annotations

from astrbot.api import logger

from ..core.decimal import round1
from ..core.maisoul_probe import resolve_maisoul_api

# 连续同向情绪增益表（maisoul core/emotion.FEEDBACK_GAIN 同款，索引 |pfb|）
FEEDBACK_GAIN: tuple[float, ...] = (1.0, 1.0, 1.1, 1.2, 1.4, 1.7, 1.9, 2.0)

# 等级跃迁 → (情绪词, 强度)：按跨越档数取 1 档 / ≥2 档
_LEVEL_WORDS_UP = {1: ("安心", 0.4), 2: ("开心", 0.6)}
_LEVEL_WORDS_DOWN = {1: ("委屈", 0.5), 2: ("悲伤", 0.7)}


def _maisoul_api(context):
    """麦麦插件 facade（无则 None）——探测唯一实现在 core/maisoul_probe。

    事故复盘：本函数曾与 judge_service 的模型联动各持一份
    探测副本，桥修复鸭子兜底时漏了那路，本地化目录名下三路联动静默
    失效数日。静默契约不变，但每个降级点补 debug 留痕——降级与可观
    测性不冲突。"""
    api = resolve_maisoul_api(context)
    if api is None:
        logger.debug("[心弦] 麦麦 facade 未探测到，联动本轮空转")
    return api


async def modulate_delta(context, group_id: str, delta: float) -> float:
    """方向①：按麦麦 pfb 调制评审增量（同向放大/异向缩小），失败原值返回。"""
    if not delta:
        return delta
    api = _maisoul_api(context)
    if api is None:
        return delta
    try:
        fb = await api.get_feedback(str(group_id))
    except Exception as e:
        logger.debug(f"[心弦] 读麦麦 pfb 失败，增量不调制: {e}")
        return delta
    if not isinstance(fb, dict):
        # 畸形真值（字符串/对象等）：fb.get 会抛 AttributeError——模块契约
        # 是任何失败都返回原值，绝不把异常抛回评审链路（Sourcery #64）
        logger.debug(f"[心弦] 麦麦 pfb 畸形（{type(fb).__name__}），增量不调制")
        return delta
    try:
        pfb = int(fb.get("pfb") or 0)
    except Exception:
        return delta
    if not pfb:
        return delta
    gain = FEEDBACK_GAIN[min(abs(pfb), len(FEEDBACK_GAIN) - 1)]
    same_dir = (delta > 0) == (pfb > 0)
    out = delta * gain if same_dir else delta / gain
    return round1(out)


async def notify_level_change(
    context, group_id: str, before_level, after_level, order: dict[str, int]
) -> bool:
    """方向②：等级跃迁（序位变化）→ 向麦麦注入情绪事件。返回是否注入成功。"""
    try:
        i0 = order.get(before_level.name)
        i1 = order.get(after_level.name)
        if i0 is None or i1 is None or i0 == i1:
            return False
        steps = 2 if abs(i1 - i0) >= 2 else 1
        word, intensity = (_LEVEL_WORDS_UP if i1 > i0 else _LEVEL_WORDS_DOWN)[steps]
    except Exception:
        return False
    api = _maisoul_api(context)
    if api is None:
        return False
    try:
        ok = bool(
            await api.apply_emotion_event(str(group_id), word, intensity=intensity)
        )
        if ok:
            logger.info(
                f"[心弦] 等级跃迁 {before_level.name}→{after_level.name}，"
                f"已注入麦麦情绪事件「{word}」(强度 {intensity})"
            )
        return ok
    except Exception as e:
        logger.debug(f"[心弦] 注入麦麦情绪事件失败（跃迁推送跳过）: {e}")
        return False
