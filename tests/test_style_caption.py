"""叠字红线：版面自己造（文案再长也放得下）、画面一个像素不动、同一份输入画两次一样，
外加中文折行的三条排版规则（行首禁则、行尾禁则、不孤字）。

用户裁决 2026-09-07："把'留白'从'求模型'改成'自己做'。生成侧不再要求留白带"。
此前这里守的是反面——"量版面、放不下就整张失败去重生成"；那条门禁连同它的两个用例
随裁决作废，换成本文件这几条。

折行那几条是 2026-09-08 加的：138㎡ 真跑样张把 `绿植` 断成 `绿` / `植`（按字宽硬切，
没有任何中文排版规则）。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from render2d_worker.models import PlanCopy
from render2d_worker.style_caption import (
    _NO_LINE_END,
    _NO_LINE_START,
    _ORPHAN_TAIL_CHARS,
    BLANK_ROW_TOLERANCE,
    TEXT_PAD_SHARE,
    StyleCaptionError,
    _wrap,
    render_caption,
)

_COPY = PlanCopy(
    title="暖光小院",
    summary="推开家门，每个角落都自然舒展",
    tips=["玄关放个换鞋凳", "阳台选细腿家具", "厨卫都有窗更清爽"],
)

_WIDTH, _HEIGHT = 900, 1400

_REAL_COPY = PlanCopy(
    title="暖光小院",
    summary="一进门就松一口气，每个角落都妥帖安放生活日常。",
    tips=[
        "次卧三扇窗，晾晒和通风都敞亮",
        "主卧一扇窗，配好窗帘更静谧",
        "阳台细长但通透，种点绿植刚刚好",
    ],
)
"""138㎡ 真跑那一份文案（2026-09-07 样张上的原字），折行那几条门禁盯的就是它。"""

_REAL_TIP_PER_LINE = 11
"""真图上小贴士一行放几个字。**算出来的不是拍的**：成图宽 1888 →
字号 int(1888×0.023)=43，一栏 1888//3=629，可用 int(629×0.82)=515，515//43=11。"""

_WRAP_SAMPLES = (
    *_REAL_COPY.tips,
    _REAL_COPY.summary,
    "玄关放个换鞋凳，回家先坐下换鞋，鞋柜留一格常穿鞋",
    "厨房、卫生间都有窗；洗完澡开窗，潮气散得快。",
    "主卧朝南（一整面窗），午后光能铺到床尾",
    "客厅够方正，沙发怎么摆都不挡道！",
    "暖光小院里的一整天从清晨的光落在餐桌上开始",
)
"""折行规则要在这些句子上都成立。混了逗号、顿号、分号、括号、感叹号与一句不带标点的长句。"""

_WRAP_WIDTHS = range(4, 17)
"""每句都从 4 字一行试到 16 字一行。真图是 11（小贴士）与 29（总结），两头都罩住。

**为什么不从 1 试起**：一行 1~3 个字时"标点不许领头"本身就可能无解（连着三个标点、
一行放两个字，怎么排都得有一行由标点起头）。那是图窄到不成立的那一类，由
`StyleCaptionError` 管，不由折行管。"""


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


# ---------------------------------------------------------------------------
# 折行：中文排版那三条规则（2026-09-08 加）
# ---------------------------------------------------------------------------


def test_the_word_that_got_split_on_the_real_sample_stays_whole() -> None:
    """`绿植` 曾被断成 `绿` / `植`——138㎡ 真跑样张，2026-09-08 修。这条盯的就是它。

    折行改成"先按句读断"之后，`阳台细长但通透，` 与 `种点绿植刚刚好` 各自成行，
    断点落在逗号上，词整个留在一行里。
    """
    lines = _wrap("阳台细长但通透，种点绿植刚刚好", _REAL_TIP_PER_LINE)

    assert lines == ["阳台细长但通透，", "种点绿植刚刚好"]
    assert any("绿植" in line for line in lines), f"`绿植` 被拆到两行：{lines}"


def test_the_three_real_tips_break_at_their_comma() -> None:
    """三条真小贴士都断在自己那个逗号上——两行是两个完整短句，不是两截。"""
    wrapped = [_wrap(tip, _REAL_TIP_PER_LINE) for tip in _REAL_COPY.tips]

    assert wrapped == [
        ["次卧三扇窗，", "晾晒和通风都敞亮"],
        ["主卧一扇窗，", "配好窗帘更静谧"],
        ["阳台细长但通透，", "种点绿植刚刚好"],
    ]


def test_punctuation_never_starts_a_line() -> None:
    """行首禁则：句读与收尾的括号引号不许领头一行。中文排版的基本规则。"""
    for text in _WRAP_SAMPLES:
        for per_line in _WRAP_WIDTHS:
            lines = _wrap(text, per_line)
            for line in lines:
                assert line[0] not in _NO_LINE_START, (
                    f"`{line[0]}` 领了一行的头（{per_line} 字一行）：{lines}"
                )


def test_an_opening_bracket_never_ends_a_line() -> None:
    """行尾禁则：起头的括号引号不许留在行尾——它得跟着它领的那段走。"""
    for text in _WRAP_SAMPLES:
        for per_line in _WRAP_WIDTHS:
            lines = _wrap(text, per_line)
            for line in lines:
                assert line[-1] not in _NO_LINE_END, (
                    f"`{line[-1]}` 留在了行尾（{per_line} 字一行）：{lines}"
                )

    # 断点被禁则往回退了一格：满行本该切在 `（` 后面，退成整个括号连着下一行走
    assert _wrap("主卧朝南（一整面窗），午后光能铺到床尾", 5)[0] == "主卧朝南"


def test_a_lone_tail_character_gets_pulled_back() -> None:
    """孤字：末行只剩一两个字就把最后两行匀一匀，不吊一个字在那儿。

    这句不带标点，走的是"按字断"那一路：21 个字按 10 字一行硬切是 10/10/1，
    末行那个 `始` 是孤字；匀完成 10/6/5。
    """
    lines = _wrap("暖光小院里的一整天从清晨的光落在餐桌上开始", 10)

    assert lines == ["暖光小院里的一整天从", "清晨的光落在", "餐桌上开始"]
    assert len(lines[-1]) > _ORPHAN_TAIL_CHARS


def test_wrapping_never_drops_a_character_nor_overflows_the_line() -> None:
    """折行只是插换行：字一个不少、顺序不变，且没有一行超过给的字数。

    禁则与孤字都是"把断点往回退"，退过头就会吞字或撑破栏——这条是它俩的兜底。
    """
    for text in _WRAP_SAMPLES:
        for per_line in _WRAP_WIDTHS:
            lines = _wrap(text, per_line)
            assert "".join(lines) == text, f"折行改了字：{lines}"
            assert all(len(line) <= per_line for line in lines), (
                f"有一行超过 {per_line} 字：{lines}"
            )


def test_a_line_ending_in_a_comma_sits_optically_centered() -> None:
    """全角标点的墨只占格子左下角，右边三分之二是空的。

    按字宽居中，带标点收尾的那一行整体偏左——138㎡ 真图实测 16px（字号 43，0.37 个字宽），
    同一条小贴士的两行肉眼就不对齐。改成按墨心居中之后两行都落回栏心。
    这里按 `render_caption` 同源的口径重算字号行高，逐行量墨迹左右缘。
    """
    result = render_caption(_page(0.28, 0.30), _REAL_COPY)

    tip_px = max(int(_WIDTH * 0.023), 12)
    tip_line = int(tip_px * 1.5)
    column = _WIDTH // len(_REAL_COPY.tips)
    with Image.open(io.BytesIO(result.image_png)) as out:
        band = out.convert("L").crop(
            (0, result.height_px - result.bottom_band_px, _WIDTH, result.height_px)
        )
    top = (result.bottom_band_px - 2 * tip_line) // 2

    for row in range(2):  # 三条贴士这一份文案都是两行
        for index in range(len(_REAL_COPY.tips)):
            cell = band.crop(
                (
                    index * column,
                    top + row * tip_line,
                    (index + 1) * column,
                    top + (row + 1) * tip_line,
                )
            )
            ink = cell.point(lambda value: 255 if value < 160 else 0).getbbox()
            assert ink is not None, f"第 {index + 1} 栏第 {row + 1} 行没画上字"
            assert abs((ink[0] + ink[2]) / 2 - column / 2) <= 2, (
                f"第 {index + 1} 栏第 {row + 1} 行墨心偏出栏心 2px：{ink}"
            )
