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
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel, ConfigDict

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

_RGB = tuple[int, int, int]


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
    grid_step_px: int | None = None
    """底纹网格间距：None＝不铺。"""
    grid_rgb: _RGB = (0, 0, 0)
    wall_glow_rgb: _RGB | None = None
    """墙线外发光色（画在墙底下、往外洇 3px）：None＝不发光。"""
    label_ink_rgb: _RGB
    label_halo_rgb: _RGB
    note_ink_rgb: _RGB


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
"""藏青底、白线、细网格——工程蓝图的那套语言。"""

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


class PlanBriefError(Exception):
    """说明图画不出来。响亮失败，不给一张缺字的图。"""

    def __init__(self, details: list[str]) -> None:
        super().__init__("；".join(details))
        self.details = details


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
        plan.paste(Image.new("RGB", plan.size, style.wall_ink_rgb), (0, 0), edge_mask)
    return plan


def render_plan_brief(
    master: PlanMaster, notes: list[PlanNote], style: BriefStyle = BRIEF_STYLE_DEFAULT
) -> PlanBrief:
    """母版 + 批注 → 功能说明图。同样的输入画两次逐字节相同。"""
    if not notes:
        raise PlanBriefError(["一条批注都没有：那就只是母版，不是说明图"])
    known_rooms = {room.name for room in master.rooms}
    stray = [note.room for note in notes if note.room not in known_rooms]
    if stray:
        raise PlanBriefError(
            [f"批注挂在母版上没有的房间上：{'、'.join(stray)}——挂不上去就不画，不悄悄丢掉"]
        )

    label_font = find_cjk_font(ROOM_LABEL_PX)
    note_font = find_cjk_font(NOTE_TEXT_PX)

    plan = _styled_plan(master, style)

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

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False)
    return PlanBrief(
        image_png=buffer.getvalue(),
        width_px=canvas.width,
        height_px=canvas.height,
        note_count=len(notes),
    )
