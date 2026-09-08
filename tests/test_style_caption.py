"""叠字红线：版面自己造（文案再长也放得下）、画面一个像素不动、同一份输入画两次一样。

用户裁决 2026-09-07："把'留白'从'求模型'改成'自己做'。生成侧不再要求留白带"。
此前这里守的是反面——"量版面、放不下就整张失败去重生成"；那条门禁连同它的两个用例
随裁决作废，换成本文件这几条。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from render2d_worker.models import PlanCopy
from render2d_worker.style_caption import (
    BLANK_ROW_TOLERANCE,
    TEXT_PAD_SHARE,
    StyleCaptionError,
    render_caption,
)

_COPY = PlanCopy(
    title="暖光小院",
    summary="推开家门，每个角落都自然舒展",
    tips=["玄关放个换鞋凳", "阳台选细腿家具", "厨卫都有窗更清爽"],
)

_WIDTH, _HEIGHT = 900, 1400


def _page(top_blank_share: float, bottom_blank_share: float) -> bytes:
    """造一张：上下留白、中间画面。画面用高对比噪点，明暗跨度接近满量程。"""
    page = Image.new("RGB", (_WIDTH, _HEIGHT), (247, 244, 238))
    pen = ImageDraw.Draw(page)
    top = int(_HEIGHT * top_blank_share)
    bottom = _HEIGHT - int(_HEIGHT * bottom_blank_share)
    for y in range(top, bottom, 3):
        pen.line([(0, y), (_WIDTH, y)], fill=(10, 10, 10), width=2)
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    return buffer.getvalue()


def test_the_page_grows_by_the_two_bands_and_the_art_is_untouched() -> None:
    source_png = _page(0.28, 0.30)
    result = render_caption(source_png, _COPY)

    assert result.top_band_px > 0 and result.bottom_band_px > 0
    with (
        Image.open(io.BytesIO(result.image_png)) as out,
        Image.open(io.BytesIO(source_png)) as source,
    ):
        assert out.width == _WIDTH
        assert out.height == _HEIGHT + result.top_band_px + result.bottom_band_px
        # 画面区逐像素与输入相同：不缩放、不拉伸、不裁切——那是业主家的户型
        art = out.crop((0, result.top_band_px, _WIDTH, result.top_band_px + _HEIGHT))
        assert art.tobytes() == source.convert("RGB").tobytes()


def test_art_aspect_ratio_survives() -> None:
    """画面等比不变形：画面区的宽高与输入逐字相同。成图比例随白边变，是另一回事。"""
    result = render_caption(_page(0.28, 0.30), _COPY)

    art_height = result.height_px - result.top_band_px - result.bottom_band_px
    assert (result.width_px, art_height) == (_WIDTH, _HEIGHT)


def test_a_frame_filling_image_with_no_blank_at_all_still_gets_its_caption() -> None:
    """生成侧不再留白之后的常态：画面铺满整幅。从前这一张判不过，要整张重生成。"""
    result = render_caption(_page(0.0, 0.0), _COPY)

    assert result.height_px > _HEIGHT
    art_height = result.height_px - result.top_band_px - result.bottom_band_px
    assert art_height == _HEIGHT


def test_a_very_long_copy_still_fits_because_the_bands_grow() -> None:
    """文案再长也放得下——白边按排完的高度算出来，不是拍一个数。"""
    long_copy = PlanCopy(
        title="暖光小院里的一整天从清晨的光落在餐桌上开始" * 3,
        summary="推开家门，每个角落都自然舒展；玄关到客厅一条直线，动线不打架" * 6,
        tips=["玄关放个换鞋凳，回家先坐下换鞋，鞋柜留一格常穿鞋" * 4] * 3,
    )

    result = render_caption(_page(0.0, 0.0), long_copy)
    short = render_caption(_page(0.0, 0.0), _COPY)

    assert result.top_band_px > short.top_band_px
    assert result.bottom_band_px > short.bottom_band_px
    art_height = result.height_px - result.top_band_px - result.bottom_band_px
    assert art_height == _HEIGHT


def test_bands_leave_the_padding_share_above_and_below_the_text() -> None:
    """白边＝字块实测高 ×(1 + 上下各一份余量)——高度是算出来的，不是拍的。"""
    result = render_caption(_page(0.28, 0.30), _COPY)

    # 标题一行 + 总结一行；行高口径与 style_caption 同源，允许取整误差 1px
    title_px = max(int(_WIDTH * 0.062), 24)
    summary_px = max(int(_WIDTH * 0.028), 14)
    text_px = int(title_px * 1.25) + int(summary_px * 1.5)
    assert abs(result.top_band_px - int(text_px * (1 + 2 * TEXT_PAD_SHARE))) <= 1


def test_an_image_too_narrow_for_one_character_fails_loud() -> None:
    """还能触发 StyleCaptionError 的只剩"图本身不成立"这一类：响亮失败，不裁字也不缩字。"""
    buffer = io.BytesIO()
    Image.new("RGB", (20, 30), (247, 244, 238)).save(buffer, format="PNG")

    with pytest.raises(StyleCaptionError, match="横向放不下"):
        render_caption(buffer.getvalue(), _COPY)


def test_blank_tolerance_sits_between_paper_grain_and_artwork() -> None:
    """判据要落在分离带上，不能骑在边界。

    真跑实测：留白带的行内明暗跨度中位 20（纸纹颗粒），画面带中位 237。首版取 12 卡在纸纹上，
    把一张留了白的图判成一点白都没留。它现在管的是"哪一段是纯纸、可以拿来当纸纹样本"。
    """
    assert 20 < BLANK_ROW_TOLERANCE < 237


def test_same_input_renders_byte_identical() -> None:
    page = _page(0.28, 0.30)

    assert render_caption(page, _COPY).image_png == render_caption(page, _COPY).image_png
