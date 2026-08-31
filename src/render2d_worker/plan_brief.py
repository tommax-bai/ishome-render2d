"""功能说明图：母版 + 房间名 + 批注，**全部确定性画上去**。

与风格图分工写死：**风格图归生成式**（模型画画面、一个字不写），**说明图归确定性**
（代码画字，坐标算得准）。说明的**内容**仍然是模型产的——产在别处、引得到事实、过了机检；
这一层只负责把它画到该在的位置上，一个字也不发明。

**房间名画在锚点上，零对齐风险**：锚点是母版从房间遮罩算出来的质心，而底图就是母版本身——
两者同一套坐标，不存在"生成图漂了一点导致标注指错房间"那个问题。那个问题只属于风格图。

**批注排在图下方、按房间名认领**，不用序号：序号一换位置就指向别的东西，而"【玄关】"
自己说得清它说的是哪间（同"命名禁纯序号"那条的同一理由）。
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from render2d_worker.cjk_font import find_cjk_font
from render2d_worker.models import PlanBrief, PlanMaster, PlanNote

ROOM_LABEL_PX = 30
"""房间名字号。母版长边 1600 时约当图宽的 2%，看得清又不盖住家具位。"""

NOTE_TEXT_PX = 28
NOTE_LINE_GAP_PX = 18
PANEL_PAD_PX = 40
_INK = (26, 26, 26)
_PAPER = (255, 255, 255)
_HALO = (255, 255, 255)
_HALO_OFFSETS = ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, -2), (-2, 2), (2, 2))
"""房间名描一圈白边：底图有淡灰房间色，纯黑字压上去边缘发糊。"""


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
) -> None:
    x, y = xy
    for offset_x, offset_y in _HALO_OFFSETS:
        pen.text((x + offset_x, y + offset_y), text, font=font, fill=_HALO, anchor=anchor)
    pen.text((x, y), text, font=font, fill=_INK, anchor=anchor)


def _wrap(text: str, per_line: int) -> list[str]:
    """按字数折行。中文没有词边界，按字数折是确定的；英文混排的折点等真出现了再说。"""
    return [text[at : at + per_line] for at in range(0, len(text), per_line)] or [""]


def render_plan_brief(master: PlanMaster, notes: list[PlanNote]) -> PlanBrief:
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

    with Image.open(io.BytesIO(master.master_png)) as base:
        plan = base.convert("RGB")

    per_line = max(1, (plan.width - 2 * PANEL_PAD_PX) // NOTE_TEXT_PX)
    lines = [line for note in notes for line in _wrap(f"【{note.room}】{note.text}", per_line)]
    panel_height = PANEL_PAD_PX * 2 + len(lines) * (NOTE_TEXT_PX + NOTE_LINE_GAP_PX)

    canvas = Image.new("RGB", (plan.width, plan.height + panel_height), _PAPER)
    canvas.paste(plan, (0, 0))
    pen = ImageDraw.Draw(canvas)

    for room in master.rooms:
        _draw_halo_text(pen, (room.anchor_x_px, room.anchor_y_px), room.name, label_font, "mm")

    y = plan.height + PANEL_PAD_PX
    for line in lines:
        pen.text((PANEL_PAD_PX, y), line, font=note_font, fill=_INK)
        y += NOTE_TEXT_PX + NOTE_LINE_GAP_PX

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False)
    return PlanBrief(
        image_png=buffer.getvalue(),
        width_px=canvas.width,
        height_px=canvas.height,
        note_count=len(notes),
    )
