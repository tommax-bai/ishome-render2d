"""功能说明图：母版 + 房间名 + 批注，**全部确定性画上去**。

与风格图分工写死：**风格图归生成式**（模型画画面、一个字不写），**说明图归确定性**
（代码画字，坐标算得准）。说明的**内容**仍然是模型产的——产在别处、引得到事实、过了机检；
这一层只负责把它画到该在的位置上，一个字也不发明。

**房间名画在锚点上，零对齐风险**：锚点是母版从房间遮罩算出来的质心，而底图就是母版本身——
两者同一套坐标，不存在"生成图漂了一点导致标注指错房间"那个问题。那个问题只属于风格图。

**批注排在图下方、按房间名认领**，不用序号：序号一换位置就指向别的东西，而"【玄关】"
自己说得清它说的是哪间（同"命名禁纯序号"那条的同一理由）。

**观感是样式表里的一行，几何一个坐标不碰**（:class:`BriefStyle`）：默认那一行就是白底灰房
黑墙的本来观感，候选变体与它并列。**默认输出一个字节不变**——变体是给业主拍板用的样张，
拍板之前发出去的那条路照旧（守卫测试盯住默认行的每个值）。

**整页图纸版式也是样式的一部分**（:class:`BriefSheet`，2026-09-01"功能说明图走制图蓝图风"
用户裁决）：拍定蓝图方向后附三条改进——墙体不要实心粗带（双线描边＋墙芯处理）、版面左右
要留白、版面要补信息层。信息层全是**确定性元素**：标题栏、坐标刻度、面积占比小表、图例、
页脚口径（"概念方案 · 尺寸以实测为准"，《第一阶段视觉提案与Prompt说明》的既有落款）。
**数字全部来自入参，这一层一个数不发明**：标题栏的数是数出来的（房间数、批注数），占比表
的数由调用方随派发传入；今天入参里没有的（楼盘名、绝对面积、朝向、比例尺）不上版面——
宁缺不编。
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Literal, NamedTuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel, ConfigDict, model_validator

from render2d_worker.cjk_font import find_cjk_font
from render2d_worker.models import PlanBrief, PlanMaster, PlanNote

ROOM_LABEL_PX = 30
"""房间名字号。母版长边 1600 时约当图宽的 2%，看得清又不盖住家具位。"""

NOTE_TEXT_PX = 28
NOTE_LINE_GAP_PX = 18
PANEL_PAD_PX = 40
_HALO_OFFSETS = ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, -2), (-2, 2), (2, 2))
"""房间名描一圈边：底图有房间底色，字直接压上去边缘发糊。"""

_WALL_VALUE = 0
_ROOM_VALUE = 236
"""母版上墙与房间底色的灰度值——逐字对齐 `plan_master` 的 `_WALL_INK` / `_ROOM_TINT`。
样式化按这两个值认区域：母版整张只有 墙/房间/纸 三种值，认值就是认区域。"""

_WALL_CORE_ERODE_PX = 5
"""双线墙的芯往里剥的窗口（MinFilter 尺寸，剥 2px）：剥完剩下的当芯上填色，
剥掉的那一圈是描边。比这薄的墙剥不出芯，整笔按描边画——细墙双线只会糊。"""

_GLOW_SPREAD_PX = 7
"""墙线外发光的扩散窗口（MaxFilter 尺寸，向外 3px）。"""

_SHEET_TITLE_PX = 46
"""整页版式的图名字号。"""

_SHEET_HEADING_PX = 26
"""信息层各块小标题（房间批注/面积占比/图例）字号。"""

_SHEET_SMALL_PX = 20
"""次级文字字号：坐标刻度、图例说明、标题栏事实行、页脚。"""

_SHEET_FRAME_PAD_PX = 40
"""图框离纸边的距离。"""

_SHEET_FRAME_GAP_PX = 6
"""图框内外两道线的间距——工程图纸的双线框。"""

_TITLE_STRIP_PX = _SHEET_TITLE_PX + 23
"""标题栏总高：图名 + 距线 18 + 粗线 2 + 距细线 2 + 细线 1。"""

_SHEET_TICK_BAND_PX = 40
"""户型图上沿给坐标刻度留的带高；不标刻度时收窄成 `_SHEET_PLAIN_BAND_PX`。"""

_SHEET_PLAIN_BAND_PX = 16
_SHEET_TICK_LEN_PX = 6
"""刻度短线长度（贴着户型图外沿）。"""

_SHEET_ROW_PX = 40
"""占比表行高。"""

_SHEET_BAR_PX = 12
"""占比条厚度。"""

_SHEET_SWATCH_W_PX = 84
_SHEET_SWATCH_H_PX = 30
_SHEET_LEGEND_ROW_PX = 44
_HEADING_RULE_GAP_PX = 14
_HEADING_BODY_GAP_PX = 20
_PANEL_HEAD_PX = _SHEET_HEADING_PX + _HEADING_RULE_GAP_PX + 2 + _HEADING_BODY_GAP_PX
"""信息块头部总高：小标题 + 距线 + 线宽 2 + 线到正文。"""

_SHEET_COL_GAP_PX = 64
"""信息排底时左右两栏之间的空。"""

_SHEET_PANEL_GAP_PX = 72
"""信息排侧时户型与侧栏之间的空。"""

_SHEET_BLOCK_GAP_PX = 40
"""批注/占比/图例几块摞放时块与块之间的空。"""

_SHEET_SIDE_PANEL_W_PX = 600
"""信息排侧时侧栏的宽。"""

_SHEET_INFO_GAP_PX = 56
"""户型图到信息层的空。"""

_SHEET_FOOTER_GAP_PX = 44
"""信息层（或图纸主体）到页脚分线的空。"""

_RGB = tuple[int, int, int]


class BriefSheet(BaseModel):
    """整页图纸版式：留白、图框、信息层排位与配色——**全是数据，几何一个坐标不碰**。

    挂在 :attr:`BriefStyle.sheet` 上：None＝老版式（母版下面挂一条批注），给了＝整页图纸。
    信息层只收确定性元素，**这一层一个数不发明**：标题栏的数是数出来的（房间数、批注数），
    占比表的数由 :func:`render_plan_brief` 的 `room_shares` 传入，拿不到就不画那一块。
    """

    model_config = ConfigDict(frozen=True)

    info_at: Literal["bottom", "right"]
    """信息层排哪儿：户型图下方两栏（"bottom"），或右侧一条侧栏（"right"）。"""
    side_margin_px: int
    """版面左右留白（户型图到纸边）——"户型几乎顶满画布"就是这个值太小的观感。"""
    frame_rgb: _RGB
    """图框（内外双线）颜色。"""
    accent_rgb: _RGB
    """标题栏底线与各块小标题底线的强调色。"""
    muted_rgb: _RGB
    """次级文字色：坐标刻度、图例说明、标题栏事实行、页脚。"""
    rule_rgb: _RGB
    """细分隔线与占比条底轨的颜色。"""
    bar_rgb: _RGB
    """占比条的填充色。"""
    tick_every_px: int | None = None
    """坐标刻度间距（沿户型图外沿标 A/B/C… 与 1/2/3…）：None＝不标。
    取网格间距的整倍数才对得上格线。"""
    title_text: str
    """图名。图纸自己的名目，固定串——不是模型产物，也不是文案。"""
    footer_text: str
    """页脚口径。"概念方案 · 尺寸以实测为准"出自《第一阶段视觉提案与Prompt说明》。"""


class BriefStyle(BaseModel):
    """功能说明图的一套画法：颜色、底纹、墙的笔法——**全是数据，几何一个坐标不碰**。

    加一版观感＝加一行数据；房间名与批注在每一版里都在（只加不减信息）。
    """

    model_config = ConfigDict(frozen=True)

    name: str
    paper_rgb: _RGB
    """版面底色：屋外与批注区。"""
    room_zone_rgbs: tuple[_RGB, ...]
    """房间底色，按遮罩序号轮换分区；只给一个值＝全屋统一色。"""
    wall_ink_rgb: _RGB
    wall_core_rgb: _RGB | None = None
    """双线墙的芯色：描边用 `wall_ink_rgb`、芯上填这个；None＝实心一笔。"""
    wall_hatch_step_px: int | None = None
    """墙芯斜线剖面纹的间距：None＝不铺。纹铺在双线芯里，所以要求 `wall_core_rgb` 已给。"""
    wall_hatch_rgb: _RGB | None = None
    """剖面纹线色，与 `wall_hatch_step_px` 同给同缺。"""
    grid_step_px: int | None = None
    """底纹网格间距：None＝不铺。"""
    grid_rgb: _RGB = (0, 0, 0)
    wall_glow_rgb: _RGB | None = None
    """墙线外发光色（画在墙底下、往外洇 3px）：None＝不发光。"""
    label_ink_rgb: _RGB
    label_halo_rgb: _RGB
    note_ink_rgb: _RGB
    sheet: BriefSheet | None = None
    """整页图纸版式：None＝老版式（母版下面挂一条批注）。"""

    @model_validator(mode="after")
    def _hatch_rides_on_a_core(self) -> BriefStyle:
        """剖面纹铺在墙芯里：间距与颜色同给同缺，且必须先有双线芯。样式配错建模时就响。"""
        if (self.wall_hatch_step_px is None) != (self.wall_hatch_rgb is None):
            raise ValueError("剖面纹的间距与颜色要么都给、要么都不给")
        if self.wall_hatch_step_px is not None and self.wall_core_rgb is None:
            raise ValueError("剖面纹铺在墙芯（wall_core_rgb）里：没有芯就没有铺纹的地方")
        return self


BRIEF_STYLE_DEFAULT = BriefStyle(
    name="默认线稿",
    paper_rgb=(255, 255, 255),
    room_zone_rgbs=((236, 236, 236),),
    wall_ink_rgb=(0, 0, 0),
    label_ink_rgb=(26, 26, 26),
    label_halo_rgb=(255, 255, 255),
    note_ink_rgb=(26, 26, 26),
)
"""发给业主的那条路。值即母版历来的观感；改这一行＝改默认输出，守卫测试会拦。"""

BRIEF_STYLE_BLUEPRINT = BriefStyle(
    name="制图蓝图",
    paper_rgb=(13, 35, 64),
    room_zone_rgbs=((23, 49, 84),),
    wall_ink_rgb=(240, 248, 255),
    grid_step_px=32,
    grid_rgb=(28, 60, 100),
    label_ink_rgb=(235, 243, 252),
    label_halo_rgb=(13, 35, 64),
    note_ink_rgb=(198, 218, 240),
)
"""藏青底、白线、细网格——工程蓝图的那套语言。2026-09-01 拍定为功能说明图的方向；
改进版在下面的"制图蓝图·整页"几行，这一行留作那次比对的原样。"""

BRIEF_STYLE_PRECISION = BriefStyle(
    name="精密制图",
    paper_rgb=(255, 255, 255),
    room_zone_rgbs=(
        (238, 242, 247),
        (247, 241, 233),
        (236, 245, 239),
        (247, 238, 241),
        (240, 240, 247),
        (245, 245, 236),
    ),
    wall_ink_rgb=(30, 32, 36),
    wall_core_rgb=(186, 190, 196),
    label_ink_rgb=(30, 32, 36),
    label_halo_rgb=(255, 255, 255),
    note_ink_rgb=(40, 44, 48),
)
"""白底、双线墙带灰芯、房间按分区上淡色——线宽分级讲究的那版。"""

BRIEF_STYLE_TECH = BriefStyle(
    name="科技暗调",
    paper_rgb=(15, 19, 26),
    room_zone_rgbs=(
        (26, 32, 41),
        (24, 34, 44),
        (29, 31, 43),
    ),
    wall_ink_rgb=(72, 226, 236),
    grid_step_px=40,
    grid_rgb=(29, 39, 52),
    wall_glow_rgb=(22, 66, 74),
    label_ink_rgb=(226, 243, 247),
    label_halo_rgb=(15, 19, 26),
    note_ink_rgb=(148, 198, 209),
)
"""深炭底、青色墙线带外发光、暗格网——自由发挥那版，HUD 的观感。"""

_BLUEPRINT_SHEET_BOTTOM = BriefSheet(
    info_at="bottom",
    side_margin_px=150,
    frame_rgb=(112, 146, 188),
    accent_rgb=(106, 152, 203),
    muted_rgb=(150, 175, 205),
    rule_rgb=(46, 78, 116),
    bar_rgb=(106, 152, 203),
    tick_every_px=128,
    title_text="户型功能说明图",
    footer_text="概念方案 · 尺寸以实测为准",
)
_BLUEPRINT_SHEET_RIGHT = _BLUEPRINT_SHEET_BOTTOM.model_copy(update={"info_at": "right"})

BRIEF_STYLE_BLUEPRINT_POCHE_BOTTOM = BriefStyle(
    name="制图蓝图·双线淡芯·信息排底",
    paper_rgb=(13, 35, 64),
    room_zone_rgbs=((23, 49, 84),),
    wall_ink_rgb=(240, 248, 255),
    wall_core_rgb=(74, 106, 148),
    grid_step_px=32,
    grid_rgb=(28, 60, 100),
    label_ink_rgb=(235, 243, 252),
    label_halo_rgb=(13, 35, 64),
    note_ink_rgb=(198, 218, 240),
    sheet=_BLUEPRINT_SHEET_BOTTOM,
)
"""改进版候选（2026-09-01 裁决三条改进都在）：墙体双线描边＋墙芯淡填（poché）、
左右各留 150px、信息层排户型下方两栏（左：批注＋图例，右：占比表）。
默认切换时点＝业主看过改进版样张点头时，在那之前默认线稿不变。"""

BRIEF_STYLE_BLUEPRINT_HATCH_RIGHT = BriefStyle(
    name="制图蓝图·双线剖面纹·信息排侧",
    paper_rgb=(13, 35, 64),
    room_zone_rgbs=((23, 49, 84),),
    wall_ink_rgb=(240, 248, 255),
    wall_core_rgb=(30, 58, 96),
    wall_hatch_step_px=7,
    wall_hatch_rgb=(150, 180, 214),
    grid_step_px=32,
    grid_rgb=(28, 60, 100),
    label_ink_rgb=(235, 243, 252),
    label_halo_rgb=(13, 35, 64),
    note_ink_rgb=(198, 218, 240),
    sheet=_BLUEPRINT_SHEET_RIGHT,
)
"""改进版候选：墙芯铺 45° 剖面纹（工程剖切画法），信息层排右侧一条侧栏。"""

BRIEF_STYLE_BLUEPRINT_POCHE_RIGHT = BriefStyle(
    name="制图蓝图·双线淡芯·信息排侧",
    paper_rgb=(13, 35, 64),
    room_zone_rgbs=((23, 49, 84),),
    wall_ink_rgb=(240, 248, 255),
    wall_core_rgb=(74, 106, 148),
    grid_step_px=32,
    grid_rgb=(28, 60, 100),
    label_ink_rgb=(235, 243, 252),
    label_halo_rgb=(13, 35, 64),
    note_ink_rgb=(198, 218, 240),
    sheet=_BLUEPRINT_SHEET_RIGHT,
)
"""改进版候选：墙体淡芯同上、信息层排侧——墙体画法与版面排布两个轴各自能挑。"""


class PlanBriefError(Exception):
    """说明图画不出来。响亮失败，不给一张缺字的图。"""

    def __init__(self, details: list[str]) -> None:
        super().__init__("；".join(details))
        self.details = details


class _SheetFonts(NamedTuple):
    """整页版式用到的一套字号。`body` 与老版式的批注字号同源，字级只加不换。"""

    title: ImageFont.FreeTypeFont
    heading: ImageFont.FreeTypeFont
    body: ImageFont.FreeTypeFont
    small: ImageFont.FreeTypeFont
    label: ImageFont.FreeTypeFont


def _draw_halo_text(
    pen: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    anchor: str,
    style: BriefStyle,
) -> None:
    x, y = xy
    for offset_x, offset_y in _HALO_OFFSETS:
        pen.text(
            (x + offset_x, y + offset_y), text, font=font, fill=style.label_halo_rgb, anchor=anchor
        )
    pen.text((x, y), text, font=font, fill=style.label_ink_rgb, anchor=anchor)


def _wrap(text: str, per_line: int) -> list[str]:
    """按字数折行。中文没有词边界，按字数折是确定的；英文混排的折点等真出现了再说。"""
    return [text[at : at + per_line] for at in range(0, len(text), per_line)] or [""]


def _share_label(share: float) -> str:
    """0.1918 →「19.2%」：×100、一位小数、符号紧排——同报告线"比率印给业主用百分数"
    （2026-08-31 裁决）的口径，也与批注正文里的印法（"占内部面积 19.2%"）逐字对得上。"""
    return f"{share * 100:.1f}%"


def _room_zone_layer(rooms_mask: Image.Image, style: BriefStyle) -> Image.Image:
    """房间底色层：遮罩序号 → 分区色轮换。序号 0（不属于任何房间）落到版面底色上。"""
    zones = style.room_zone_rgbs
    channels = [
        rooms_mask.point(
            [
                (zones[(index - 1) % len(zones)][channel] if index else style.paper_rgb[channel])
                for index in range(256)
            ]
        )
        for channel in range(3)
    ]
    return Image.merge("RGB", channels)


def _diagonal_hatch_mask(size: tuple[int, int], step_px: int) -> Image.Image:
    """整幅 45° 斜线的遮罩：与墙芯遮罩相乘，纹就只落在芯里。"""
    width, height = size
    mask = Image.new("L", size, 0)
    pen = ImageDraw.Draw(mask)
    for offset in range(-height, width, step_px):
        pen.line(((offset, height), (offset + height, 0)), fill=255)
    return mask


def _styled_plan(master: PlanMaster, style: BriefStyle) -> Image.Image:
    """把母版按样式上色。区域按母版的三种灰度值认（墙/房间/纸），几何一个坐标不碰。"""
    with Image.open(io.BytesIO(master.master_png)) as base:
        values = base.convert("L")
    with Image.open(io.BytesIO(master.rooms_png)) as base:
        rooms_mask = base.convert("L")

    wall_mask = values.point(lambda value: 255 if value == _WALL_VALUE else 0)
    room_mask = values.point(lambda value: 255 if value == _ROOM_VALUE else 0)

    plan = Image.new("RGB", values.size, style.paper_rgb)
    plan.paste(_room_zone_layer(rooms_mask, style), (0, 0), room_mask)

    if style.grid_step_px is not None:
        pen = ImageDraw.Draw(plan)
        for x in range(0, plan.width, style.grid_step_px):
            pen.line(((x, 0), (x, plan.height)), fill=style.grid_rgb)
        for y in range(0, plan.height, style.grid_step_px):
            pen.line(((0, y), (plan.width, y)), fill=style.grid_rgb)

    if style.wall_glow_rgb is not None:
        glow_mask = wall_mask.filter(ImageFilter.MaxFilter(_GLOW_SPREAD_PX))
        plan.paste(Image.new("RGB", plan.size, style.wall_glow_rgb), (0, 0), glow_mask)

    plan.paste(Image.new("RGB", plan.size, style.wall_ink_rgb), (0, 0), wall_mask)
    if style.wall_core_rgb is not None:
        core_mask = wall_mask.filter(ImageFilter.MinFilter(_WALL_CORE_ERODE_PX))
        # 描边＝墙去掉芯剩下的那一圈；细墙剥不出芯，整笔仍是描边色
        edge_mask = ImageChops.subtract(wall_mask, core_mask)
        plan.paste(Image.new("RGB", plan.size, style.wall_core_rgb), (0, 0), core_mask)
        if style.wall_hatch_step_px is not None and style.wall_hatch_rgb is not None:
            hatch_mask = ImageChops.multiply(
                core_mask, _diagonal_hatch_mask(plan.size, style.wall_hatch_step_px)
            )
            plan.paste(Image.new("RGB", plan.size, style.wall_hatch_rgb), (0, 0), hatch_mask)
        plan.paste(Image.new("RGB", plan.size, style.wall_ink_rgb), (0, 0), edge_mask)
    return plan


def _strip_canvas(
    plan: Image.Image,
    master: PlanMaster,
    notes: list[PlanNote],
    style: BriefStyle,
    label_font: ImageFont.FreeTypeFont,
    note_font: ImageFont.FreeTypeFont,
) -> Image.Image:
    """老版式：母版原样在上、批注一条挂图下。默认样式走的就是这条路——**字节不变**，
    这里的每一笔与样式表引入前逐字相同。"""
    per_line = max(1, (plan.width - 2 * PANEL_PAD_PX) // NOTE_TEXT_PX)
    lines = [line for note in notes for line in _wrap(f"【{note.room}】{note.text}", per_line)]
    panel_height = PANEL_PAD_PX * 2 + len(lines) * (NOTE_TEXT_PX + NOTE_LINE_GAP_PX)

    canvas = Image.new("RGB", (plan.width, plan.height + panel_height), style.paper_rgb)
    canvas.paste(plan, (0, 0))
    pen = ImageDraw.Draw(canvas)

    for room in master.rooms:
        _draw_halo_text(
            pen, (room.anchor_x_px, room.anchor_y_px), room.name, label_font, "mm", style
        )

    y = plan.height + PANEL_PAD_PX
    for line in lines:
        pen.text((PANEL_PAD_PX, y), line, font=note_font, fill=style.note_ink_rgb)
        y += NOTE_TEXT_PX + NOTE_LINE_GAP_PX
    return canvas


def _draw_heading(
    pen: ImageDraw.ImageDraw,
    width_px: int,
    text: str,
    muted_suffix: str | None,
    style: BriefStyle,
    sheet: BriefSheet,
    fonts: _SheetFonts,
) -> int:
    """信息块的头：小标题（可带次级小字）＋强调底线。返回正文起点 y（＝`_PANEL_HEAD_PX`）。"""
    pen.text((0, 0), text, font=fonts.heading, fill=style.label_ink_rgb)
    if muted_suffix is not None:
        lead = round(pen.textlength(text, font=fonts.heading))
        pen.text(
            (lead + 12, _SHEET_HEADING_PX - _SHEET_SMALL_PX),
            muted_suffix,
            font=fonts.small,
            fill=sheet.muted_rgb,
        )
    rule_y = _SHEET_HEADING_PX + _HEADING_RULE_GAP_PX
    pen.line(((0, rule_y), (width_px - 1, rule_y)), fill=sheet.accent_rgb, width=2)
    return _PANEL_HEAD_PX


def _notes_panel(
    width_px: int,
    notes: list[PlanNote],
    style: BriefStyle,
    sheet: BriefSheet,
    fonts: _SheetFonts,
) -> Image.Image:
    """批注块：既有五条判语原样进新版面，仍按【房间名】认领。"""
    per_line = max(1, width_px // NOTE_TEXT_PX)
    lines = [line for note in notes for line in _wrap(f"【{note.room}】{note.text}", per_line)]
    height = _PANEL_HEAD_PX + len(lines) * (NOTE_TEXT_PX + NOTE_LINE_GAP_PX)
    panel = Image.new("RGB", (width_px, height), style.paper_rgb)
    pen = ImageDraw.Draw(panel)
    y = _draw_heading(pen, width_px, "房间批注", None, style, sheet, fonts)
    for line in lines:
        pen.text((0, y), line, font=fonts.body, fill=style.note_ink_rgb)
        y += NOTE_TEXT_PX + NOTE_LINE_GAP_PX
    return panel


def _share_panel(
    width_px: int,
    room_shares: Mapping[str, float],
    style: BriefStyle,
    sheet: BriefSheet,
    fonts: _SheetFonts,
) -> Image.Image:
    """面积占比小表：房间名、占比条、百分数。数从入参来（房间遮罩份额，上游既有求值），
    条按最大份额归一——数值印在右列，条只管一眼看出大小关系。"""
    ordered = sorted(room_shares.items(), key=lambda item: (-item[1], item[0]))
    labels = [_share_label(value) for _, value in ordered]
    height = _PANEL_HEAD_PX + len(ordered) * _SHEET_ROW_PX
    panel = Image.new("RGB", (width_px, height), style.paper_rgb)
    pen = ImageDraw.Draw(panel)
    y = _draw_heading(pen, width_px, "面积占比", "占内部面积", style, sheet, fonts)

    name_w = max(round(pen.textlength(name, font=fonts.body)) for name, _ in ordered)
    pct_w = max(round(pen.textlength(label, font=fonts.body)) for label in labels)
    track_x = name_w + 20
    # 名字与数字先占位，条吃剩下的；窄到吃不上时条退成 1px——表还是全的，条只是看不出长短
    track_w = max(1, width_px - track_x - pct_w - 20)
    top_share = ordered[0][1]
    for (name, value), label in zip(ordered, labels, strict=True):
        row_mid = y + _SHEET_ROW_PX // 2
        pen.text((0, row_mid), name, font=fonts.body, fill=style.note_ink_rgb, anchor="lm")
        bar_top = row_mid - _SHEET_BAR_PX // 2
        pen.rectangle(
            (track_x, bar_top, track_x + track_w - 1, bar_top + _SHEET_BAR_PX - 1),
            fill=sheet.rule_rgb,
        )
        fill_w = max(1, round(track_w * value / top_share))
        pen.rectangle(
            (track_x, bar_top, track_x + fill_w - 1, bar_top + _SHEET_BAR_PX - 1),
            fill=sheet.bar_rgb,
        )
        pen.text(
            (width_px - 1, row_mid), label, font=fonts.body, fill=style.label_ink_rgb, anchor="rm"
        )
        y += _SHEET_ROW_PX
    return panel


def _legend_rows(style: BriefStyle) -> list[tuple[str, str]]:
    """图例行：说的是**这版真怎么画的**——墙的说法跟着样式走，网格行只在真铺了网格时有。"""
    if style.wall_hatch_step_px is not None:
        wall_caption = "墙体：双线描边＋剖面纹"
    elif style.wall_core_rgb is not None:
        wall_caption = "墙体：双线描边＋墙芯淡填"
    else:
        wall_caption = "墙体：实心带"
    rows = [
        ("wall", wall_caption),
        ("opening", "门窗洞口：墙线断开处（本图不分门窗）"),
        ("room", "房间范围：底色区块"),
    ]
    if style.grid_step_px is not None:
        rows.append(("grid", "参考网格：仅助定位，不代表实际尺寸"))
    return rows


def _draw_legend_swatch(
    panel: Image.Image,
    pen: ImageDraw.ImageDraw,
    kind: str,
    origin: tuple[int, int],
    style: BriefStyle,
    sheet: BriefSheet,
) -> None:
    """一格图例样块：用与正图同一套颜色画一小段"墙的横截面"，图例即画法本身。"""
    x, y = origin
    right = x + _SHEET_SWATCH_W_PX - 1
    bottom = y + _SHEET_SWATCH_H_PX - 1
    if kind in ("wall", "opening"):
        pen.rectangle((x, y + 4, right, bottom - 4), fill=style.wall_ink_rgb)
        if style.wall_core_rgb is not None:
            core_w = _SHEET_SWATCH_W_PX - 4
            core_h = _SHEET_SWATCH_H_PX - 12
            core = Image.new("RGB", (core_w, core_h), style.wall_core_rgb)
            if style.wall_hatch_step_px is not None and style.wall_hatch_rgb is not None:
                core_pen = ImageDraw.Draw(core)
                for offset in range(-core_h, core_w, style.wall_hatch_step_px):
                    core_pen.line(
                        ((offset, core_h), (offset + core_h, 0)), fill=style.wall_hatch_rgb
                    )
            panel.paste(core, (x + 2, y + 6))
        if kind == "opening":
            gap_l = x + (_SHEET_SWATCH_W_PX - 20) // 2
            pen.rectangle((gap_l, y + 4, gap_l + 19, bottom - 4), fill=style.paper_rgb)
    elif kind == "room":
        pen.rectangle((x, y + 2, right, bottom - 2), fill=style.room_zone_rgbs[0])
        pen.rectangle((x, y + 2, right, bottom - 2), outline=sheet.rule_rgb)
    elif kind == "grid":
        pen.rectangle((x, y + 2, right, bottom - 2), outline=sheet.rule_rgb)
        for grid_x in range(x + 10, right, 10):
            pen.line(((grid_x, y + 3), (grid_x, bottom - 3)), fill=style.grid_rgb)
        for grid_y in range(y + 12, bottom - 2, 10):
            pen.line(((x + 1, grid_y), (right - 1, grid_y)), fill=style.grid_rgb)


def _legend_panel(
    width_px: int, style: BriefStyle, sheet: BriefSheet, fonts: _SheetFonts
) -> Image.Image:
    rows = _legend_rows(style)
    height = _PANEL_HEAD_PX + len(rows) * _SHEET_LEGEND_ROW_PX
    panel = Image.new("RGB", (width_px, height), style.paper_rgb)
    pen = ImageDraw.Draw(panel)
    y = _draw_heading(pen, width_px, "图例", None, style, sheet, fonts)
    for kind, caption in rows:
        swatch_top = y + (_SHEET_LEGEND_ROW_PX - _SHEET_SWATCH_H_PX) // 2
        _draw_legend_swatch(panel, pen, kind, (0, swatch_top), style, sheet)
        pen.text(
            (_SHEET_SWATCH_W_PX + 16, y + _SHEET_LEGEND_ROW_PX // 2),
            caption,
            font=fonts.small,
            fill=sheet.muted_rgb,
            anchor="lm",
        )
        y += _SHEET_LEGEND_ROW_PX
    return panel


def _draw_frame(pen: ImageDraw.ImageDraw, sheet: BriefSheet, width: int, height: int) -> None:
    """图纸双线框：外粗内细。"""
    pad = _SHEET_FRAME_PAD_PX
    pen.rectangle((pad, pad, width - 1 - pad, height - 1 - pad), outline=sheet.frame_rgb, width=2)
    inner = pad + _SHEET_FRAME_GAP_PX
    pen.rectangle(
        (inner, inner, width - 1 - inner, height - 1 - inner), outline=sheet.frame_rgb, width=1
    )


def _draw_title_strip(
    pen: ImageDraw.ImageDraw,
    style: BriefStyle,
    sheet: BriefSheet,
    fonts: _SheetFonts,
    left: int,
    right: int,
    top: int,
    room_count: int,
    note_count: int,
) -> None:
    """标题栏：图名靠左、事实行靠右、下压一粗一细两道线。事实行的数是数出来的，
    楼盘名与绝对面积今天的入参里没有——宁缺不编。"""
    pen.text((left, top), sheet.title_text, font=fonts.title, fill=style.label_ink_rgb)
    facts = f"房间 {room_count} 间 · 批注 {note_count} 条"
    pen.text(
        (right, top + _SHEET_TITLE_PX - _SHEET_SMALL_PX - 4),
        facts,
        font=fonts.small,
        fill=sheet.muted_rgb,
        anchor="ra",
    )
    rule_y = top + _SHEET_TITLE_PX + 18
    pen.line(((left, rule_y), (right, rule_y)), fill=sheet.accent_rgb, width=2)
    pen.line(((left, rule_y + 4), (right, rule_y + 4)), fill=sheet.rule_rgb, width=1)


def _draw_plan_ticks(
    pen: ImageDraw.ImageDraw,
    sheet: BriefSheet,
    plan_left: int,
    plan_top: int,
    plan_w: int,
    plan_h: int,
    fonts: _SheetFonts,
) -> None:
    """坐标刻度：上沿 A/B/C…、左沿 1/2/3…，间距对齐网格的整倍数。只是图纸的定位语言，
    不承载尺寸——没有比例尺，格数换算不出米数。"""
    if sheet.tick_every_px is None:
        return
    for index, x in enumerate(range(plan_left, plan_left + plan_w, sheet.tick_every_px)):
        letter = chr(ord("A") + index % 26)
        pen.line(
            ((x, plan_top - _SHEET_TICK_LEN_PX), (x, plan_top - 1)),
            fill=sheet.muted_rgb,
        )
        pen.text(
            (x, plan_top - _SHEET_TICK_LEN_PX - 4),
            letter,
            font=fonts.small,
            fill=sheet.muted_rgb,
            anchor="ms",
        )
    for index, y in enumerate(range(plan_top, plan_top + plan_h, sheet.tick_every_px)):
        pen.line(
            ((plan_left - _SHEET_TICK_LEN_PX, y), (plan_left - 1, y)),
            fill=sheet.muted_rgb,
        )
        pen.text(
            (plan_left - _SHEET_TICK_LEN_PX - 6, y),
            str(index + 1),
            font=fonts.small,
            fill=sheet.muted_rgb,
            anchor="rm",
        )


def _draw_footer(
    pen: ImageDraw.ImageDraw,
    sheet: BriefSheet,
    left: int,
    right: int,
    rule_y: int,
    fonts: _SheetFonts,
) -> None:
    pen.line(((left, rule_y), (right, rule_y)), fill=sheet.rule_rgb, width=1)
    pen.text(
        ((left + right) // 2, rule_y + 16),
        sheet.footer_text,
        font=fonts.small,
        fill=sheet.muted_rgb,
        anchor="ma",
    )


def _sheet_canvas(
    plan: Image.Image,
    master: PlanMaster,
    notes: list[PlanNote],
    style: BriefStyle,
    sheet: BriefSheet,
    room_shares: Mapping[str, float] | None,
    label_font: ImageFont.FreeTypeFont,
    note_font: ImageFont.FreeTypeFont,
) -> Image.Image:
    """整页图纸：图框、标题栏、留白居中的户型（带坐标刻度）、信息层、页脚。

    信息层排位两式：排底＝户型下方两栏（左：批注＋图例，右：占比表）；
    排侧＝右侧一条侧栏（批注、占比表、图例摞放）。占比数据没给就不画那一块——宁缺不编。
    """
    fonts = _SheetFonts(
        title=find_cjk_font(_SHEET_TITLE_PX),
        heading=find_cjk_font(_SHEET_HEADING_PX),
        body=note_font,
        small=find_cjk_font(_SHEET_SMALL_PX),
        label=label_font,
    )
    tick_band = _SHEET_TICK_BAND_PX if sheet.tick_every_px is not None else _SHEET_PLAIN_BAND_PX
    title_top = _SHEET_FRAME_PAD_PX + 28
    plan_top = title_top + _TITLE_STRIP_PX + tick_band

    if sheet.info_at == "bottom":
        sheet_w = plan.width + 2 * sheet.side_margin_px
        content_l = sheet.side_margin_px
        content_r = sheet_w - sheet.side_margin_px
        content_w = content_r - content_l
        notes_w = (content_w - _SHEET_COL_GAP_PX) * 14 // 25
        aside_w = content_w - _SHEET_COL_GAP_PX - notes_w
        notes_panel = _notes_panel(notes_w, notes, style, sheet, fonts)
        legend_panel = _legend_panel(notes_w, style, sheet, fonts)
        share_panel = (
            _share_panel(aside_w, room_shares, style, sheet, fonts) if room_shares else None
        )
        left_col_h = notes_panel.height + _SHEET_BLOCK_GAP_PX + legend_panel.height
        info_h = max(left_col_h, share_panel.height if share_panel is not None else 0)
        plan_x = content_l
        info_top = plan_top + plan.height + _SHEET_INFO_GAP_PX
        footer_rule_y = info_top + info_h + _SHEET_FOOTER_GAP_PX
    else:
        sheet_w = (
            2 * sheet.side_margin_px + plan.width + _SHEET_PANEL_GAP_PX + _SHEET_SIDE_PANEL_W_PX
        )
        content_l = sheet.side_margin_px
        content_r = sheet_w - sheet.side_margin_px
        notes_panel = _notes_panel(_SHEET_SIDE_PANEL_W_PX, notes, style, sheet, fonts)
        legend_panel = _legend_panel(_SHEET_SIDE_PANEL_W_PX, style, sheet, fonts)
        share_panel = (
            _share_panel(_SHEET_SIDE_PANEL_W_PX, room_shares, style, sheet, fonts)
            if room_shares
            else None
        )
        column_h = notes_panel.height + _SHEET_BLOCK_GAP_PX + legend_panel.height
        if share_panel is not None:
            column_h += _SHEET_BLOCK_GAP_PX + share_panel.height
        plan_x = content_l
        info_top = plan_top
        footer_rule_y = plan_top + max(plan.height, column_h) + _SHEET_FOOTER_GAP_PX

    sheet_h = footer_rule_y + 16 + _SHEET_SMALL_PX + 12 + _SHEET_FRAME_PAD_PX
    canvas = Image.new("RGB", (sheet_w, sheet_h), style.paper_rgb)
    pen = ImageDraw.Draw(canvas)
    _draw_frame(pen, sheet, sheet_w, sheet_h)
    _draw_title_strip(
        pen, style, sheet, fonts, content_l, content_r, title_top, len(master.rooms), len(notes)
    )
    canvas.paste(plan, (plan_x, plan_top))
    for room in master.rooms:
        _draw_halo_text(
            pen,
            (plan_x + room.anchor_x_px, plan_top + room.anchor_y_px),
            room.name,
            fonts.label,
            "mm",
            style,
        )
    _draw_plan_ticks(pen, sheet, plan_x, plan_top, plan.width, plan.height, fonts)

    if sheet.info_at == "bottom":
        canvas.paste(notes_panel, (content_l, info_top))
        canvas.paste(legend_panel, (content_l, info_top + notes_panel.height + _SHEET_BLOCK_GAP_PX))
        if share_panel is not None:
            canvas.paste(share_panel, (content_r - share_panel.width, info_top))
    else:
        panel_x = content_l + plan.width + _SHEET_PANEL_GAP_PX
        stack_y = info_top
        canvas.paste(notes_panel, (panel_x, stack_y))
        stack_y += notes_panel.height + _SHEET_BLOCK_GAP_PX
        if share_panel is not None:
            canvas.paste(share_panel, (panel_x, stack_y))
            stack_y += share_panel.height + _SHEET_BLOCK_GAP_PX
        canvas.paste(legend_panel, (panel_x, stack_y))

    _draw_footer(pen, sheet, content_l, content_r, footer_rule_y, fonts)
    return canvas


def _check_room_shares(room_shares: Mapping[str, float], known_rooms: set[str]) -> None:
    """占比数据要么配得齐、要么别给：挂错房间、缺一间、值出 (0, 1]，都当场响。"""
    problems: list[str] = []
    stray = sorted(set(room_shares) - known_rooms)
    if stray:
        problems.append(f"占比挂在母版上没有的房间上：{'、'.join(stray)}")
    missing = sorted(known_rooms - set(room_shares))
    if missing:
        problems.append(f"占比缺了 {'、'.join(missing)}：少一间的表把份额读歪，要么全给、要么不给")
    off = [f"{name}={value}" for name, value in sorted(room_shares.items()) if not 0 < value <= 1]
    if off:
        problems.append(f"占比不在 (0, 1] 里：{'、'.join(off)}——上游给错了就响，不悄悄画")
    if problems:
        raise PlanBriefError(problems)


def render_plan_brief(
    master: PlanMaster,
    notes: list[PlanNote],
    style: BriefStyle = BRIEF_STYLE_DEFAULT,
    room_shares: Mapping[str, float] | None = None,
) -> PlanBrief:
    """母版 + 批注 → 功能说明图。同样的输入画两次逐字节相同。

    `room_shares`＝各房间占内部面积的份额（0~1，上游房间遮罩既有求值的产物），
    只有整页版式的占比小表用它；不给＝那一块不画（宁缺不编），给了就必须配得齐。
    """
    if not notes:
        raise PlanBriefError(["一条批注都没有：那就只是母版，不是说明图"])
    known_rooms = {room.name for room in master.rooms}
    stray = [note.room for note in notes if note.room not in known_rooms]
    if stray:
        raise PlanBriefError(
            [f"批注挂在母版上没有的房间上：{'、'.join(stray)}——挂不上去就不画，不悄悄丢掉"]
        )
    if room_shares is not None:
        _check_room_shares(room_shares, known_rooms)

    label_font = find_cjk_font(ROOM_LABEL_PX)
    note_font = find_cjk_font(NOTE_TEXT_PX)

    plan = _styled_plan(master, style)
    if style.sheet is None:
        canvas = _strip_canvas(plan, master, notes, style, label_font, note_font)
    else:
        canvas = _sheet_canvas(
            plan, master, notes, style, style.sheet, room_shares, label_font, note_font
        )

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False)
    return PlanBrief(
        image_png=buffer.getvalue(),
        width_px=canvas.width,
        height_px=canvas.height,
        note_count=len(notes),
    )
