"""心弦好感度 - 排行图片渲染（PIL）。

把群内好感度排行画成**列优先网格**：左上最高、向下递减、再第二列同理；
查询人高亮。每格：等级色温条 + 名次 + 昵称(QQ后4位) + 右对齐好感度（按色温轴着色）。
标题下为冷→暖渐变「弦线」，与 WebUI 面板同一套视觉语言。
字体自动探测 CJK（容器内自带 + 其他插件）；找不到退回 PIL 默认（仅 ASCII）。
"""

from __future__ import annotations

import math
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 临时图带专属前缀，渲染前顺手清理 1 小时前的旧图（图片发出后文件即无用，
# 否则常驻进程会往 /tmp 无限累积）
_TMP_PREFIX = "xinxian_rank_"

# 自动探测的 CJK 字体候选（容器内常见位置 + 其他插件自带）
_CJK_CANDIDATES = [
    "/AstrBot/data/plugin_data/astrbot_plugin_shoubanhua/fonts/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
]

# 千咲（朽叶千咲）配色：黑长直黑发 + 红瞳 + 红黑剪刀 + 湮灭暗调 → 深底 + 绯红强调
_BG = (26, 24, 30)  # 页面底：近黑微紫（湮灭/暗调）
_TITLE = (236, 230, 232)  # 标题：近白
_ACCENT = (172, 36, 50)  # 千咲红（红瞳 / 红黑剪刀）：高亮与强调
_CELL_BG = (40, 36, 46)  # 单元格底：深紫黑
_CELL_BORDER = (74, 46, 56)  # 单元格边：暗红紫
_TEXT = (228, 224, 230)  # 正文：浅
_HL_BG = _ACCENT  # 查询人高亮：千咲红
_HL_BORDER = (214, 64, 79)  # 高亮描边：亮绯
_HL_TEXT = (255, 255, 255)  # 高亮文字：白
_EMPTY = (150, 142, 150)
_DIM = (122, 115, 128)  # 弱化：名次/页脚
_TOP3 = (238, 84, 99)  # 前三名：亮绯

# 七级好感色温轴（深底可读版）：冷蓝紫(厌恶) → 灰 → 玫瑰 → 亮绯(挚爱)
# 与 WebUI 面板同源，按深底提亮
_LEVEL_COLORS = {
    "厌恶": (139, 151, 224),
    "陌生": (168, 163, 174),
    "认识": (195, 156, 201),
    "友好": (217, 138, 160),
    "亲密": (232, 122, 140),
    "挚友": (238, 84, 99),
    "挚爱": (255, 96, 112),
}
_RAMP = list(_LEVEL_COLORS.values())  # 弦线渐变停靠点


def _level_color(row: dict):
    """行 → 色温轴颜色；level 缺失时按默认阈值回退估算。"""
    lv = str(row.get("level") or "")
    if lv in _LEVEL_COLORS:
        return _LEVEL_COLORS[lv]
    try:
        f = float(row.get("favor") or 0)
    except (TypeError, ValueError):
        f = 0.0
    for name, lo in (
        ("挚爱", 95),
        ("挚友", 80),
        ("亲密", 55),
        ("友好", 30),
        ("认识", 10),
        ("陌生", 0),
    ):
        if f >= lo:
            return _LEVEL_COLORS[name]
    return _LEVEL_COLORS["厌恶"]


def _load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    for p in [path] + _CJK_CANDIDATES if path else _CJK_CANDIDATES:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _last4(uid) -> str:
    s = str(uid or "")
    return s[-4:] if len(s) >= 4 else s


def _favor_str(v) -> str:
    try:
        f = float(v)
    except Exception:
        return str(v)
    return ("%g" % f) if f == int(f) else ("%.1f" % f)


def _fit(draw, text: str, font, max_w: int) -> str:
    """按实际文字宽度截断 text 到 max_w 内（超出加 …），保证不溢出格子。"""
    if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
        return text
    lo, hi = 1, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textbbox((0, 0), text[:mid] + "…", font=font)[2] <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[: max(lo, 1)] + "…"


def _text_vh(
    draw, x: int, y_center: int, text: str, font, fill, *, right_x: int | None = None
):
    """垂直居中画文本；right_x 给定时右对齐到该 x。"""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (right_x - tw - bbox[0]) if right_x is not None else (x - bbox[0])
    draw.text((tx, y_center - th / 2 - bbox[1]), text, font=font, fill=fill)


def _gradient_string(
    img: Image.Image, x0: int, y: int, width: int, height: int = 3
) -> None:
    """冷→暖渐变「弦线」（圆角），停靠点取色温轴七级色。"""
    grad = Image.new("RGB", (width, height))
    gd = ImageDraw.Draw(grad)
    last = len(_RAMP) - 1
    for gx in range(width):
        t = gx / max(width - 1, 1) * last
        i = min(int(t), last - 1)
        f = t - i
        c = tuple(
            round(_RAMP[i][k] + (_RAMP[i + 1][k] - _RAMP[i][k]) * f) for k in range(3)
        )
        gd.line([(gx, 0), (gx, height)], fill=c)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=height // 2, fill=255
    )
    img.paste(grad, (x0, y), mask)


def _cleanup_stale_tmp() -> None:
    """best-effort 删除 1 小时前生成的旧排行图；任何失败忽略。"""
    try:
        cutoff = time.time() - 3600
        for p in Path(tempfile.gettempdir()).glob(f"{_TMP_PREFIX}*.png"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except Exception:
        pass


def render_ranking(
    rows: list[dict],
    querier_id,
    *,
    font_path: str = "",
    rows_per_col: int = 12,
    title: str = "本群好感度排行",
) -> str:
    """渲染排行图片，返回临时 PNG 路径。

    rows: list[dict]（含 user_id/favor，**已按 favor 降序**；有 level 则按色温轴着色）。
    querier_id: 当前查询人 QQ（该格高亮）。
    """
    _cleanup_stale_tmp()
    n = len(rows)
    cols = max(1, math.ceil(n / rows_per_col)) if n else 1
    rows_in_col = math.ceil(n / cols) if n else 1

    cell_w, cell_h = 372, 48
    gap_x, gap_y = 14, 12
    pad = 28
    title_h = 68
    footer_h = 36

    img_w = pad * 2 + cols * cell_w + max(0, cols - 1) * gap_x
    img_h = (
        pad * 2
        + title_h
        + rows_in_col * cell_h
        + max(0, rows_in_col - 1) * gap_y
        + footer_h
    )
    img = Image.new("RGB", (img_w, img_h), _BG)
    draw = ImageDraw.Draw(img)

    f_title = _load_font(font_path, 28)
    f_cell = _load_font(font_path, 20)
    f_rank = _load_font(font_path, 18)
    f_small = _load_font(font_path, 16)

    # ---- 头部：红瞳圆点 + 标题 + 右侧人数 + 渐变弦线 ----
    dot_cx, dot_cy = pad + 7, pad + 17
    draw.ellipse(
        [dot_cx - 10, dot_cy - 10, dot_cx + 10, dot_cy + 10], fill=(58, 34, 42)
    )
    draw.ellipse([dot_cx - 5, dot_cy - 5, dot_cx + 5, dot_cy + 5], fill=_ACCENT)
    draw.text((pad + 24, pad + 2), title, font=f_title, fill=_TITLE)
    if n:
        _text_vh(draw, 0, dot_cy, f"共 {n} 人", f_small, _DIM, right_x=img_w - pad)
    _gradient_string(img, pad, pad + 46, img_w - pad * 2)
    querier = str(querier_id or "")

    if n == 0:
        draw.text((pad, pad + title_h + 20), "暂无成员", font=f_cell, fill=_EMPTY)

    # ---- 单元格：色温条 + 名次 + 昵称(QQ后4位) + 右对齐好感 ----
    for i, r in enumerate(rows):
        col = i // rows_in_col
        row = i % rows_in_col
        x = pad + col * (cell_w + gap_x)
        y = pad + title_h + row * (cell_h + gap_y)
        cy = y + cell_h / 2
        is_q = str(r.get("user_id", "")) == querier
        draw.rounded_rectangle(
            [x, y, x + cell_w, y + cell_h],
            radius=10,
            fill=_HL_BG if is_q else _CELL_BG,
            outline=_HL_BORDER if is_q else _CELL_BORDER,
        )
        lc = _level_color(r)
        fg = _HL_TEXT if is_q else _TEXT

        # 左：等级色温条
        draw.rounded_rectangle(
            [x + 10, y + 11, x + 14, y + cell_h - 11],
            radius=2,
            fill=_HL_TEXT if is_q else lc,
        )
        # 名次（前三亮绯）
        rank_color = _HL_TEXT if is_q else (_TOP3 if i < 3 else _DIM)
        _text_vh(draw, x + 24, cy, str(i + 1), f_rank, rank_color)

        # 好感（右对齐，色温轴着色）→ 先量宽，再给昵称留位
        favor_txt = _favor_str(r.get("favor", 0))
        fw = draw.textbbox((0, 0), favor_txt, font=f_cell)[2]
        favor_color = _HL_TEXT if is_q else lc
        _text_vh(draw, 0, cy, favor_txt, f_cell, favor_color, right_x=x + cell_w - 16)

        # 昵称(QQ后4位)：QQ 段永不截断
        last4 = _last4(r.get("user_id", ""))
        suffix = f"({last4})"
        suffix_w = draw.textbbox((0, 0), suffix, font=f_cell)[2]
        nick_x = x + 66
        nick_max = (x + cell_w - 16 - fw - 14) - nick_x
        nick = str(r.get("nickname") or "").strip()
        nick = (
            _fit(draw, nick, f_cell, max(nick_max - suffix_w - 6, 30)) if nick else ""
        )
        label = f"{nick} {suffix}" if nick else suffix
        _text_vh(draw, nick_x, cy, label, f_cell, fg)

    # ---- 页脚 ----
    fy = img_h - pad - footer_h / 2 + 4
    _text_vh(draw, 0, fy, "心弦 · 好感度", f_small, _DIM, right_x=img_w - pad)

    path = tempfile.NamedTemporaryFile(
        prefix=_TMP_PREFIX, suffix=".png", delete=False
    ).name
    img.save(path, "PNG")
    return path
