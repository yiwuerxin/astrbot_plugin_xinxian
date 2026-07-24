"""心弦好感度 - 排行图片渲染（PIL）。

把群内好感度排行画成**列优先网格**：左上最高、向下递减、再第二列同理；
查询人高亮。每格 `(QQ后4位): 好感度`。
字体自动探测 CJK（容器内自带 + 其他插件）；找不到退回 PIL 默认（仅 ASCII）。
"""

from __future__ import annotations

import math
import os
import tempfile

from PIL import Image, ImageDraw, ImageFont

# 自动探测的 CJK 字体候选（容器内常见位置 + 其他插件自带）
_CJK_CANDIDATES = [
    "/AstrBot/data/plugin_data/astrbot_plugin_shoubanhua/fonts/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
]

# 千咲（朽叶千咲）配色：黑长直黑发 + 红瞳 + 红黑剪刀 + 湮灭暗调 → 深底 + 绯红强调
_BG = (26, 24, 30)            # 页面底：近黑微紫（湮灭/暗调）
_TITLE = (236, 230, 232)      # 标题：近白
_ACCENT = (172, 36, 50)       # 千咲红（红瞳 / 红黑剪刀）：高亮与强调
_CELL_BG = (40, 36, 46)       # 单元格底：深紫黑
_CELL_BORDER = (74, 46, 56)   # 单元格边：暗红紫
_TEXT = (228, 224, 230)       # 正文：浅
_HL_BG = _ACCENT              # 查询人高亮：千咲红
_HL_TEXT = (255, 255, 255)    # 高亮文字：白
_EMPTY = (150, 142, 150)


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
    return text[:max(lo, 1)] + "…"


def render_ranking(
    rows: list[dict],
    querier_id,
    *,
    font_path: str = "",
    rows_per_col: int = 12,
    title: str = "本群好感度排行",
) -> str:
    """渲染排行图片，返回临时 PNG 路径。

    rows: list[dict]（含 user_id/favor，**已按 favor 降序**）。
    querier_id: 当前查询人 QQ（该格高亮）。
    """
    n = len(rows)
    cols = max(1, math.ceil(n / rows_per_col)) if n else 1
    rows_in_col = math.ceil(n / cols) if n else 1

    cell_w, cell_h = 360, 46
    gap_x, gap_y = 14, 12
    pad = 24
    title_h = 56

    img_w = pad * 2 + cols * cell_w + max(0, cols - 1) * gap_x
    img_h = pad * 2 + title_h + rows_in_col * cell_h + max(0, rows_in_col - 1) * gap_y
    img = Image.new("RGB", (img_w, img_h), _BG)
    draw = ImageDraw.Draw(img)

    f_title = _load_font(font_path, 26)
    f_cell = _load_font(font_path, 20)

    draw.text((pad, pad + 6), title, font=f_title, fill=_TITLE)
    draw.line([(pad, pad + title_h - 8), (img_w - pad, pad + title_h - 8)], fill=_ACCENT, width=3)
    querier = str(querier_id or "")

    if n == 0:
        draw.text((pad, pad + title_h + 20), "暂无成员", font=f_cell, fill=_EMPTY)
    for i, r in enumerate(rows):
        col = i // rows_in_col
        row = i % rows_in_col
        x = pad + col * (cell_w + gap_x)
        y = pad + title_h + row * (cell_h + gap_y)
        is_q = str(r.get("user_id", "")) == querier
        draw.rounded_rectangle(
            [x, y, x + cell_w, y + cell_h], radius=10,
            fill=_HL_BG if is_q else _CELL_BG, outline=_CELL_BORDER,
        )
        nick = str(r.get("nickname") or "").strip()
        last4 = _last4(r.get("user_id", ""))
        suffix = f"({last4}): {_favor_str(r.get('favor', 0))}"  # QQ后4位 + 好感：永不截断
        if nick:
            suffix_w = draw.textbbox((0, 0), suffix, font=f_cell)[2]
            nick = _fit(draw, nick, f_cell, max(cell_w - 24 - suffix_w - 12, 36))
            txt = f"{nick} {suffix}"
        else:
            txt = suffix
        color = _HL_TEXT if is_q else _TEXT
        bbox = draw.textbbox((0, 0), txt, font=f_cell)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (x + (cell_w - tw) / 2 - bbox[0], y + (cell_h - th) / 2 - bbox[1]),
            txt, font=f_cell, fill=color,
        )

    path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    img.save(path, "PNG")
    return path
