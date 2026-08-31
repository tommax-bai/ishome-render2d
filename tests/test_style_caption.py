"""叠字红线：放不下就不叠（宁可重生成，不把字压在他家客厅上）、空白判据要落在分离带上。"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from render2d_worker.models import PlanCopy
from render2d_worker.style_caption import (
    BLANK_ROW_TOLERANCE,
    StyleCaptionError,
    render_caption,
)

_COPY = PlanCopy(
    title="暖光小院",
    summary="推开家门，每个角落都自然舒展",
    tips=["玄关放个换鞋凳", "阳台选细腿家具", "厨卫都有窗更清爽"],
)


def _page(top_blank_share: float, bottom_blank_share: float) -> bytes:
    """造一张：上下留白、中间画面。画面用高对比噪点，明暗跨度接近满量程。"""
    width, height = 900, 1400
    page = Image.new("RGB", (width, height), (247, 244, 238))
    pen = ImageDraw.Draw(page)
    top = int(height * top_blank_share)
    bottom = height - int(height * bottom_blank_share)
    for y in range(top, bottom, 3):
        pen.line([(0, y), (width, y)], fill=(10, 10, 10), width=2)
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    return buffer.getvalue()


def test_captions_land_when_there_is_room() -> None:
    result = render_caption(_page(0.28, 0.30), _COPY)

    assert result.top_blank_px > 0 and result.bottom_blank_px > 0
    with Image.open(io.BytesIO(result.image_png)) as out:
        assert out.size == (900, 1400)  # 底图原尺寸，不另加画布


def test_no_room_at_the_top_fails_loud() -> None:
    """失败形态是"重生成一次"，不是"字歪在画面上"。"""
    with pytest.raises(StyleCaptionError, match="没有一段连续空白"):
        render_caption(_page(0.01, 0.30), _COPY)


def test_no_room_at_the_bottom_fails_loud() -> None:
    with pytest.raises(StyleCaptionError, match="没有一段连续空白"):
        render_caption(_page(0.28, 0.005), _COPY)


def test_blank_tolerance_sits_between_paper_grain_and_artwork() -> None:
    """判据要落在分离带上，不能骑在边界。

    真跑实测：留白带的行内明暗跨度中位 20（纸纹颗粒），画面带中位 237。首版取 12 卡在纸纹上，
    把一张留了白的图判成一点白都没留。
    """
    assert 20 < BLANK_ROW_TOLERANCE < 237


def test_same_input_renders_byte_identical() -> None:
    page = _page(0.28, 0.30)

    assert render_caption(page, _COPY).image_png == render_caption(page, _COPY).image_png
