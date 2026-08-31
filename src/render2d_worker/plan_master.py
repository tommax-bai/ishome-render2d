"""母版绘制：几何 → 母版 PNG + 两张机器可读层 + 房间锚点。**确定性，零模型调用。**

母版是**几何唯一源**：后面所有风格图强制回读它，"户型有没有漂"因此从模型主观判断变成
可量化门槛（生成图与房间遮罩逐房间比对重合度）。所以这一层的判据不是"好看"，是**对得上**：
同一份几何画两次必须逐字节相同，画出来的墙必须落在原图的墙上。

**母版上不写字**。房间名、批注、标题是后面那一步的事——母版只把"字该落在哪儿"（房间锚点）
算出来交出去。这样母版不依赖任何字体，本机与服务器画出来的是同一张图。

**洞画成墙断开，不画门扇窗框**：产出侧这一层只分洞在外墙还是内墙，不分门与窗；
画法各家不同，凭"是个洞"画不出门扇。门窗画法的时点写死＝产出侧补上门窗识别那一批。
"""

from __future__ import annotations

import io
import json

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from render2d_worker.models import (
    FloorplanGeometry,
    PlanMaster,
    PlanOpening,
    PlanWall,
    RoomAnchor,
)

DEFAULT_LONG_SIDE_PX = 1600
"""母版长边像素。够大到墙厚不被取整抹平，又不至于让后面的生成步吃一张巨图。"""

DEFAULT_MARGIN_PX = 24
"""四周留白：外墙贴着画布边缘时，遮罩比对与配准都会在边界上出毛病。"""

_WALL_INK = 0
_PAPER = 255
_ROOM_TINT = 236
"""母版上房间内部的淡灰：一眼看得出哪儿是屋里、哪儿是屋外，又不至于压过墙线。"""

_MIN_WALL_PX = 2
"""墙再薄也得占住两像素——一像素的线在缩放与遮罩比对里会断。"""

_OPENING_BLEED_PX = 1
"""擦门洞时比墙厚多擦一点：正好等厚会在洞口两侧留下一像素的墙渣。"""

_WINDOW_SLIT_SHARE = 1 / 3
"""窗缝占墙厚的比例：留三分之一，两侧各剩三分之一黑边——平面图上一眼认得出是窗。"""

MIN_OUTLINE_CLOSURE_RATIO = 0.90
"""外圈闭合率门槛。低于它即响亮失败——**外圈漏风的母版不许当几何唯一源往下游传**。

同几何提取那道自证（房间拼不满户型即失败）：判据不是"看着还行"，是一个算得出来的数。
后面每一张风格图都回读母版、"户型有没有漂"靠它量，底子上缺一段外墙，下游没有一步能发现。

**取 0.90 而不是 1.0，因为量的东西自带残差**：这个数拿房间遮罩的边界去比墙，而房间遮罩
只盖住户型内部自由面积的九成七（几何那侧的自证数），剩下的"没归着"处也会贡献边界像素，
那些地方本来就不该有墙。首个真实样例实测：几何缺四条飘窗边时 **64%**，补齐后 **94%**——
门槛落在两者中间，分得开。**样本只有一张**，复看时点＝拿到第二批样本时。"""

_OUTLINE_PROBE_PX = 5
"""量外圈时往里剥几像素取边界带。"""

_SAME_LINE_TOLERANCE = 0.006
"""判"洞在哪道墙上"时位置的容差：外轮廓给的是墙带中心线，与网格投票出来的线差半个墙厚是常态。"""

_OUTLINE_TOLERANCE_PX = 9
"""墙压在边界上的容差：墙心线与房间边界差几像素是画法本身带来的，不是漏墙。"""


class PlanMasterError(Exception):
    """母版画不出来。响亮失败，不给一张"差不多的"图——下游拿它当几何唯一源。"""

    def __init__(self, details: list[str]) -> None:
        super().__init__("；".join(details))
        self.details = details


class _Frame:
    """归一化坐标 → 母版像素。图幅裁出来、按长边缩放、四周留白。"""

    def __init__(self, geometry: FloorplanGeometry, long_side_px: int, margin_px: int) -> None:
        left, top, right, bottom = geometry.plan_box
        self._left_px = left * geometry.frame_width_px
        self._top_px = top * geometry.frame_height_px
        plan_width_px = (right - left) * geometry.frame_width_px
        plan_height_px = (bottom - top) * geometry.frame_height_px
        if plan_width_px <= 0 or plan_height_px <= 0:
            raise PlanMasterError([f"图幅是空的：{geometry.plan_box}"])
        self._scale = (long_side_px - 2 * margin_px) / max(plan_width_px, plan_height_px)
        self._margin_px = margin_px
        self.width_px = round(plan_width_px * self._scale) + 2 * margin_px
        self.height_px = round(plan_height_px * self._scale) + 2 * margin_px
        self._frame_width_px = geometry.frame_width_px
        self._frame_height_px = geometry.frame_height_px

    def x(self, x_ratio: float) -> float:
        return (x_ratio * self._frame_width_px - self._left_px) * self._scale + self._margin_px

    def y(self, y_ratio: float) -> float:
        return (y_ratio * self._frame_height_px - self._top_px) * self._scale + self._margin_px

    def across_x(self, ratio: float) -> float:
        """按图宽归一的长度（竖墙的厚度）换算到母版像素。"""
        return ratio * self._frame_width_px * self._scale

    def across_y(self, ratio: float) -> float:
        """按图高归一的长度（横墙的厚度）换算到母版像素。"""
        return ratio * self._frame_height_px * self._scale


def _band(
    frame: _Frame, item: PlanWall | PlanOpening, thickness_px: float
) -> tuple[float, float, float, float]:
    """一段墙/一个洞在母版上占的矩形。竖的横的只差一次坐标对调。"""
    half = max(thickness_px, _MIN_WALL_PX) / 2
    if item.axis == "vertical":
        center = frame.x(item.position_ratio)
        return (center - half, frame.y(item.start_ratio), center + half, frame.y(item.end_ratio))
    center = frame.y(item.position_ratio)
    return (frame.x(item.start_ratio), center - half, frame.x(item.end_ratio), center + half)


def _wall_thickness_px(frame: _Frame, wall: PlanWall) -> float:
    # 厚度的归一化分母随轴向变（竖墙按图宽、横墙按图高），换算也必须跟着变
    if wall.axis == "vertical":
        return frame.across_x(wall.thickness_ratio)
    return frame.across_y(wall.thickness_ratio)


def _lay_solid_walls(frame: _Frame, geometry: FloorplanGeometry) -> Image.Image:
    """只砌墙不开洞。外圈闭合率对着这一张量——**窗和门都是墙上的构造，不是墙没了**；
    对着开完洞的图量，一户飘窗多的房子会被自己的窗判成外墙漏风。"""
    layer = Image.new("L", (frame.width_px, frame.height_px), _PAPER)
    _brush_walls(frame, geometry, ImageDraw.Draw(layer))
    return layer


def _brush_walls(frame: _Frame, geometry: FloorplanGeometry, pen: ImageDraw.ImageDraw) -> None:
    """外轮廓与网格墙一起砌。两处都是墙，重合的段画两遍是同一笔黑。"""
    for wall in (*geometry.outline, *geometry.walls):
        pen.rectangle(_band(frame, wall, _wall_thickness_px(frame, wall)), fill=_WALL_INK)


def _lay_walls(frame: _Frame, geometry: FloorplanGeometry, ground: Image.Image) -> Image.Image:
    """在给定底子上砌墙，然后按洞在哪面墙上分两种画法。

    **内墙上的洞断开，外墙上的洞画成窗**。产出侧这一层只分洞在外墙还是内墙、不分门与窗
    （门窗画法各家不同，凭"是个洞"画不出门扇）；而这一分正好够用：内墙上的洞几乎都是门，
    断开是对的；外墙上的洞几乎都是窗，**断开就把外圈拆了**——首版全按断开画，一张 92㎡ 的
    户型外墙被九个飘窗啃得只剩碎渣，外圈是母版最要紧的一道墙。
    窗画成墙内留一条细缝（平面图的通行画法），墙因此仍是连的。

    **已知代价**：入户门开在外墙上，会被画成窗。一处，且是视觉上的一处，换回一圈闭合的外墙。
    **消这条代价的时点写死＝产出侧补上门窗识别那一批**，那时按门窗分而不按内外墙分。

    断开用的是"把砌墙之前那一层原样还回来"，不是刷白：内墙两边都是屋里，刷白会在户型正中
    开出一条白缝。
    """
    layer = ground.copy()
    pen = ImageDraw.Draw(layer)
    _brush_walls(frame, geometry, pen)
    for opening in geometry.openings:
        thickness_px = _opening_thickness_px(frame, geometry, opening)
        if opening.is_on_outer_wall:
            pen.rectangle(_band(frame, opening, thickness_px * _WINDOW_SLIT_SHARE), fill=_PAPER)
            continue
        # 门：还窄了留墙渣，还宽了把邻墙也啃掉
        box = _band(frame, opening, thickness_px)
        crop = (round(box[0]), round(box[1]), round(box[2]), round(box[3]))
        layer.paste(ground.crop(crop), crop[:2])
    return layer


def _room_ground(frame: _Frame, rooms_mask: Image.Image) -> Image.Image:
    """母版的底子：屋里淡灰、屋外白。**由房间遮罩推得**，所以两张图不会各说各话。"""
    size = (frame.width_px, frame.height_px)
    inside = rooms_mask.point(lambda index: 255 if index else 0)
    return Image.composite(Image.new("L", size, _ROOM_TINT), Image.new("L", size, _PAPER), inside)


def _opening_thickness_px(
    frame: _Frame, geometry: FloorplanGeometry, opening: PlanOpening
) -> float:
    """洞所在那道墙有多厚。找不到同位置的墙就按最厚的擦——宁可擦透，别留墙渣。"""
    same_line = [
        wall
        for wall in (*geometry.outline, *geometry.walls)
        if wall.axis == opening.axis
        and abs(wall.position_ratio - opening.position_ratio) < _SAME_LINE_TOLERANCE
    ]
    thickness_px = max(
        (_wall_thickness_px(frame, wall) for wall in same_line),
        default=max(
            (_wall_thickness_px(frame, wall) for wall in (*geometry.outline, *geometry.walls)),
            default=0.0,
        ),
    )
    return max(thickness_px, _MIN_WALL_PX) + 2 * _OPENING_BLEED_PX


def _draw_rooms(frame: _Frame, geometry: FloorplanGeometry) -> tuple[Image.Image, list[RoomAnchor]]:
    """房间遮罩索引图：像素值＝房间序号（1 起，0 是"不属于任何房间"）。

    索引图而不是每间一张：一张图说得完，也让"两间房抢同一块地方"当场看得见。
    序号从 1 起，因为 0 要留给背景——遮罩比对时"没归着"和"第一个房间"不能是同一个值。
    """
    layer = Image.new("L", (frame.width_px, frame.height_px), 0)
    pen = ImageDraw.Draw(layer)
    anchors: list[RoomAnchor] = []
    if len(geometry.rooms) > 255:
        raise PlanMasterError([f"房间数 {len(geometry.rooms)} 超出索引图能表达的 255 个"])
    for index, room in enumerate(geometry.rooms, start=1):
        for box in room.boxes:
            pen.rectangle(
                (frame.x(box[0]), frame.y(box[1]), frame.x(box[2]), frame.y(box[3])), fill=index
            )
        anchors.append(
            RoomAnchor(
                name=room.name,
                mask_index=index,
                anchor_x_px=round(frame.x(room.centroid[0])),
                anchor_y_px=round(frame.y(room.centroid[1])),
            )
        )
    return layer, anchors


def _outline_closure_ratio(rooms_mask: Image.Image, walls: Image.Image) -> float:
    """户型外轮廓有多少被墙盖住。**母版自己的自证数**，不依赖任何外部判断。

    算法：室内区域往里剥一圈得到边界带，看其中多少像素落在墙上（带几像素容差）。
    这个数低，只有一种解释——几何产物里那一段既没有墙也没有能画成墙的东西。
    """
    inside = rooms_mask.point(lambda index: 255 if index else 0)
    border = ImageChops.subtract(inside, inside.filter(ImageFilter.MinFilter(_OUTLINE_PROBE_PX)))
    ink = walls.point(lambda value: 255 if value < 128 else 0).filter(
        ImageFilter.MaxFilter(_OUTLINE_TOLERANCE_PX)
    )
    border_px = list(border.get_flattened_data())
    ink_px = list(ink.get_flattened_data())
    total = sum(1 for value in border_px if value)
    if total == 0:
        return 0.0
    covered = sum(1 for edge, wall in zip(border_px, ink_px, strict=True) if edge and wall)
    return covered / total


def room_anchors_json(rooms: list[RoomAnchor]) -> bytes:
    """房间锚点清单的字节形态。**只写在这一处**：本地写文件与写进私有桶的是同一份字节。

    两处各写一遍，就会长出"本地那份带缩进、桶里那份不带"这种差别；而下游拿哪一份都得认得。
    """
    return json.dumps([room.model_dump() for room in rooms], ensure_ascii=False, indent=2).encode(
        "utf-8"
    )


def _to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    # optimize 关掉：同一份几何画两次必须逐字节相同，压缩器的启发式不进产物
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def render_plan_master(
    geometry: FloorplanGeometry,
    *,
    long_side_px: int = DEFAULT_LONG_SIDE_PX,
    margin_px: int = DEFAULT_MARGIN_PX,
) -> PlanMaster:
    """几何 → 母版。同一份几何画两次逐字节相同（确定性是这一层的红线，不是优点）。"""
    if not geometry.walls:
        raise PlanMasterError(["几何里一段墙都没有：母版画的就是墙，没有墙就没有母版"])
    if long_side_px <= 2 * margin_px:
        raise PlanMasterError([f"长边 {long_side_px}px 装不下两边各 {margin_px}px 的留白"])

    frame = _Frame(geometry, long_side_px, margin_px)
    rooms, anchors = _draw_rooms(frame, geometry)
    paper = Image.new("L", (frame.width_px, frame.height_px), _PAPER)
    walls_only = _lay_walls(frame, geometry, paper)

    closure = _outline_closure_ratio(rooms, _lay_solid_walls(frame, geometry))
    if closure < MIN_OUTLINE_CLOSURE_RATIO:
        raise PlanMasterError(
            [
                f"外圈没闭合：户型轮廓只有 {closure:.0%} 被墙盖住"
                f"（门槛 {MIN_OUTLINE_CLOSURE_RATIO:.0%}）——几何产物里缺外墙，"
                f"不把一张外墙漏风的母版当几何唯一源往下游传"
            ]
        )

    return PlanMaster(
        # 母版：屋里淡灰、墙黑、洞断开——人一眼看得懂，也够当结构条件图用
        master_png=_to_png(_lay_walls(frame, geometry, _room_ground(frame, rooms))),
        # 墙体图层：纯墙，白纸黑墙不带房间底色，遮罩比对与配准吃这一张
        walls_png=_to_png(walls_only),
        rooms_png=_to_png(rooms),
        outline_closure_ratio=round(closure, 4),
        width_px=frame.width_px,
        height_px=frame.height_px,
        rooms=anchors,
    )
