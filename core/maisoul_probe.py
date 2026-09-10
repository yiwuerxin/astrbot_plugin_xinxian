"""跨插件探测麦麦 facade 的唯一实现（v1.31.1 下沉）。

历史事故（实际部署环境实报）：探测逻辑曾在 emotion_bridge 与
judge_service 各持一份副本，桥侧修鸭子兜底时漏了模型联动那路——
本地化（非默认英文）插件目录名下 get_registered_star 恒 None，
follow_maisoul 静默失明数日。同一语义只允许一个定义点（单一真相），
此后所有跨插件探测必须经本函数，禁止再复制。

纯鸭子实现：context 仅作参数传入、只走 getattr/callable 探测，
不 import astrbot，core 层零框架依赖。任何失败返回 None，
绝不抛异常——联动是增强不是依赖。
"""

from __future__ import annotations

# 鸭子兜底扫描的指纹：同时暴露这两个方法的 star 才认作麦麦
# （直查命中路径只验调用方所需的 required 面，与历史行为一致）
_FINGERPRINT = ("get_feedback", "apply_emotion_event")


def _has(api, names: tuple[str, ...]) -> bool:
    return api is not None and all(callable(getattr(api, n, None)) for n in names)


def resolve_maisoul_api(context, required: tuple[str, ...] = ("get_feedback",)):
    """按注册名直查麦麦 facade，查不到时鸭子兜底遍历全部 star。

    required：调用方所需的最小 API 面（默认 get_feedback，与历史上
    直查路径的校验一致；模型联动显式传 ("get_replyer_provider",)）。
    兜底扫描始终要求完整情绪指纹，防误认无关插件。找不到/方法缺失/
    探测异常一律返回 None，调用方自行降级。
    """
    try:
        api = None
        by_name = getattr(context, "get_registered_star", None)
        if callable(by_name):
            star = by_name("astrbot_plugin_maisoul")
            api = getattr(getattr(star, "star_cls", None), "api", None)
        if not _has(api, required):
            # 本地化目录名部署下名字直查恒 None（实际部署实报），
            # 遍历已加载 star 认挂了完整情绪 facade 的那个
            api = None
            scan = getattr(context, "get_all_stars", None)
            if callable(scan):
                for st in scan() or []:
                    cand = getattr(getattr(st, "star_cls", None), "api", None)
                    if _has(cand, _FINGERPRINT) and _has(cand, required):
                        api = cand
                        break
        return api if _has(api, required) else None
    except Exception:
        return None
