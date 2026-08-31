"""给风格图叠字：标题、情绪总结、三块小贴士。

**叠字前先量版面，放不下就不叠**——同"遮罩比对门禁"那条哲学：**不硬叠**。
风格图是生成式的，模板里向模型要了顶部与底部的留白，但它给多少是它的事；
量出来放不下就整张失败去重生成，而不是把字压在业主家的客厅上。

**量的是"最长一段连续空白"，不是"空白占比"**：要回答的问题是**有没有地方放字**，
而占比六成完全可能是"上三成空、中间画面、下三成空"——两段都塞不下一行标题。

**为什么这一层在 render2d 不在 imagegen**：确定性绘制归这个仓——字要落得准，坐标不能是猜的。
生成式那边只管画面、一个字不写；两边分工与"母版不写字"是同一条线。
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import cast

from PIL import Image, ImageDraw, ImageFont

from render2d_worker.cjk_font import find_cjk_font
from render2d_worker.models import PlanCopy, StyleCaptioned

TOP_SEARCH_SHARE = 0.34
BOTTOM_SEARCH_SHARE = 0.38
"""上下各在这个范围里找空白。模板向模型要的是顶部 16%、底部 22%，找的范围放宽一倍——
**模型给多少是它的事，我们要量的是"够不够"，不是"合不合规"**。"""

BLANK_ROW_TOLERANCE = 60
"""一行算不算空白：行内明暗跨度不超过它就算。

**这个数是量出来的不是拍的**：同一张真跑图上，留白带里的行跨度中位 20（纸纹颗粒），
画面带里中位 237——差一个数量级，40/60/80 三个取值给出完全相同的判定，说明判据落在分离带上
而不是骑在边界上。首版取 12，卡在纸纹上，把一张留了白的图判成一点白都没留。"""

TEXT_PAD_SHARE = 0.25
"""字块上下各留这么多余量（占字块高的比例），免得贴着画面边缘。"""

_INK = (32, 30, 28)
_SUBTLE = (96, 92, 88)


class StyleCaptionError(Exception):
    """字叠不上去。响亮失败——**宁可重生成一张，不把字压在他家客厅上**。"""

    def __init__(self, details: list[str]) -> None:
        super().__init__("；".join(details))
        self.details = details


def _paper_tone(grey: Image.Image) -> int:
    """这张纸本身多亮：取四角小块的中位灰。

    纸色因风格而异（奶油粉的纸与彩铅扫描稿的纸不是一个灰度），所以不写死一个数；
    而**四角一定是纸**——画面居中、四边留白是模板的构图约束。
    """
    patch = max(8, min(grey.width, grey.height) // 40)
    corners = [
        (0, 0),
        (grey.width - patch, 0),
        (0, grey.height - patch),
        (grey.width - patch, grey.height - patch),
    ]
    values = sorted(
        int(
            sum(
                cast(
                    "Sequence[int]",
                    list(grey.crop((x, y, x + patch, y + patch)).get_flattened_data()),
                )
            )
            / (patch * patch)
        )
        for x, y in corners
    )
    return (values[1] + values[2]) // 2


def _blank_run(image: Image.Image, top: int, bottom: int) -> tuple[int, int]:
    """这一带里最长的一段连续空白，返回（起始行, 行数）。

    一行算空白要同时满足两条：**行内明暗跨度小**，且**它跟纸一样亮**。
    只看跨度不够——**一整行纯黑的跨度也是 0**，会被当成空白；单测造图时一条贯穿全宽的黑线
    就把首版骗过去了。两条一起才是"这一行上什么都没画"。
    """
    grey = image.convert("L")
    paper = _paper_tone(grey)
    # 单通道图的像素就是 int；Pillow 的返回类型对所有模式取并集，这里按实际模式收窄
    pixels = cast(
        "Sequence[int]", list(grey.crop((0, top, image.width, bottom)).get_flattened_data())
    )
    width = image.width
    best_start, best_len, run_start, run_len = top, 0, top, 0
    for index, start in enumerate(range(0, len(pixels), width)):
        row = pixels[start : start + width]
        if (
            row
            and max(row) - min(row) <= BLANK_ROW_TOLERANCE
            and min(row) >= paper - BLANK_ROW_TOLERANCE
        ):
            run_start = run_start if run_len else top + index
            run_len += 1
            if run_len > best_len:
                best_start, best_len = run_start, run_len
        else:
            run_len = 0
    return best_start, best_len


def _wrap(text: str, per_line: int) -> list[str]:
    return [text[at : at + per_line] for at in range(0, len(text), per_line)] or [""]


def _centered(
    pen: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    center_x: int,
    top_y: int,
    line_px: int,
    fill: tuple[int, int, int],
) -> int:
    for line in lines:
        pen.text((center_x, top_y), line, font=font, fill=fill, anchor="ma")
        top_y += line_px
    return top_y


def render_caption(style_png: bytes, copy: PlanCopy) -> StyleCaptioned:
    """风格图 + 文案 → 成图。连续空白放不下这些字即整张失败。"""
    with Image.open(io.BytesIO(style_png)) as source:
        page = source.convert("RGB")

    title_px = max(int(page.width * 0.062), 24)
    summary_px = max(int(page.width * 0.028), 14)
    tip_px = max(int(page.width * 0.023), 12)
    title_line, summary_line, tip_line = (
        int(title_px * 1.25),
        int(summary_px * 1.5),
        int(tip_px * 1.5),
    )

    summary_lines = _wrap(copy.summary, max(1, int(page.width * 0.80) // summary_px))
    column = page.width // max(len(copy.tips), 1)
    tip_lines = [_wrap(tip, max(1, int(column * 0.82) // tip_px)) for tip in copy.tips]

    top_need = int((title_line + len(summary_lines) * summary_line) * (1 + TEXT_PAD_SHARE))
    bottom_need = int(
        max((len(lines) for lines in tip_lines), default=0) * tip_line * (1 + TEXT_PAD_SHARE)
    )

    top_start, top_run = _blank_run(page, 0, int(page.height * TOP_SEARCH_SHARE))
    bottom_start, bottom_run = _blank_run(
        page, page.height - int(page.height * BOTTOM_SEARCH_SHARE), page.height
    )
    if top_run < top_need or bottom_run < bottom_need:
        raise StyleCaptionError(
            [
                f"版面上没有一段连续空白放得下这些字（顶部最长空白 {top_run}px 需 {top_need}px，"
                f"底部 {bottom_run}px 需 {bottom_need}px）——重生成一张，不把字压在画面上"
            ]
        )

    title_font, summary_font, tip_font = (
        find_cjk_font(title_px),
        find_cjk_font(summary_px),
        find_cjk_font(tip_px),
    )
    pen = ImageDraw.Draw(page)
    center_x = page.width // 2

    y = top_start + (top_run - top_need) // 2 + int(top_need * TEXT_PAD_SHARE / 2)
    y = _centered(pen, [copy.title], title_font, center_x, y, title_line, _INK)
    _centered(pen, summary_lines, summary_font, center_x, y, summary_line, _SUBTLE)

    tip_top = bottom_start + (bottom_run - bottom_need) // 2 + int(bottom_need * TEXT_PAD_SHARE / 2)
    for index, lines in enumerate(tip_lines):
        _centered(pen, lines, tip_font, column * index + column // 2, tip_top, tip_line, _INK)

    buffer = io.BytesIO()
    page.save(buffer, format="PNG", optimize=False)
    return StyleCaptioned(
        image_png=buffer.getvalue(),
        width_px=page.width,
        height_px=page.height,
        top_blank_px=top_run,
        bottom_blank_px=bottom_run,
    )
