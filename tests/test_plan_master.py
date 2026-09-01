"""母版红线：外圈不闭合不出图、同一份几何画两次逐字节相同、洞按内外墙分两种画法。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from render2d_worker.models import FloorplanGeometry, PlanOpening, PlanWall, RoomOutline
from render2d_worker.plan_master import PlanMasterError, render_plan_master

_FRAME = (1000, 1000)
_LEFT, _TOP, _RIGHT, _BOTTOM = 0.2, 0.2, 0.8, 0.8
_PARTITION_X = 0.5
_THICKNESS = 0.01


def _outer_walls() -> list[PlanWall]:
    """一圈闭合的外墙：两竖两横。"""
    return [
        PlanWall(
            axis="vertical",
            position_ratio=x,
            start_ratio=_TOP,
            end_ratio=_BOTTOM,
            thickness_ratio=_THICKNESS,
        )
        for x in (_LEFT, _RIGHT)
    ] + [
        PlanWall(
            axis="horizontal",
            position_ratio=y,
            start_ratio=_LEFT,
            end_ratio=_RIGHT,
            thickness_ratio=_THICKNESS,
        )
        for y in (_TOP, _BOTTOM)
    ]


def _geometry(
    walls: list[PlanWall] | None = None,
    openings: list[PlanOpening] | None = None,
    outline: list[PlanWall] | None = None,
    plan_box: tuple[float, float, float, float] = (_LEFT, _TOP, _RIGHT, _BOTTOM),
) -> FloorplanGeometry:
    """两间房、中间一道隔墙的方户型。"""
    partition = PlanWall(
        axis="vertical",
        position_ratio=_PARTITION_X,
        start_ratio=_TOP,
        end_ratio=_BOTTOM,
        thickness_ratio=_THICKNESS,
    )
    return FloorplanGeometry(
        frame_width_px=_FRAME[0],
        frame_height_px=_FRAME[1],
        plan_box=plan_box,
        outline=outline or [],
        walls=[*(walls if walls is not None else _outer_walls()), partition],
        openings=openings or [],
        rooms=[
            RoomOutline(
                name="西屋",
                boxes=[(_LEFT, _TOP, _PARTITION_X, _BOTTOM)],
                area_ratio=0.5,
                centroid=(0.35, 0.5),
            ),
            RoomOutline(
                name="东屋",
                boxes=[(_PARTITION_X, _TOP, _RIGHT, _BOTTOM)],
                area_ratio=0.5,
                centroid=(0.65, 0.5),
            ),
        ],
        cell_coverage_ratio=0.98,
    )


def _ink_at(image: Image.Image, x: int, y: int) -> int:
    """取一像素的灰度。母版是 L 模式单通道，取回来必是 int——收窄给类型检查看。"""
    value = image.getpixel((x, y))
    assert isinstance(value, int)
    return value


def _ink_runs_px(image: Image.Image, y: int) -> list[tuple[int, int]]:
    """一行上的黑段：逐段给 (起点 x, 宽度 px)。量墙有多宽就是量这个。"""
    runs: list[tuple[int, int]] = []
    x = 0
    while x < image.width:
        if _ink_at(image, x, y) < 128:
            start = x
            while x < image.width and _ink_at(image, x, y) < 128:
                x += 1
            runs.append((start, x - start))
        else:
            x += 1
    return runs


def _outer_wall_widths_px(master_walls_png: bytes, y_ratio: float) -> tuple[int, int]:
    """在某个高度上量左右两道外墙各有多宽。**两者该一样宽**——不一样就是有一侧画重了。"""
    walls = Image.open(io.BytesIO(master_walls_png))
    y = round((y_ratio - _TOP) / (_BOTTOM - _TOP) * (walls.height - 48)) + 24
    runs = _ink_runs_px(walls, y)
    assert len(runs) >= 2, f"这一行上没量到两道外墙：{runs}"
    return runs[0][1], runs[-1][1]


def test_closed_plan_renders_with_a_closed_outline() -> None:
    master = render_plan_master(_geometry())

    assert master.outline_closure_ratio >= 0.95
    assert master.width_px > 0 and master.height_px > 0
    assert [room.name for room in master.rooms] == ["西屋", "东屋"]


def test_same_geometry_renders_byte_identical() -> None:
    # 确定性是这一层的红线不是优点：下游所有风格图回读母版，母版飘一点全线跟着飘
    first, second = render_plan_master(_geometry()), render_plan_master(_geometry())

    assert first.master_png == second.master_png
    assert first.walls_png == second.walls_png
    assert first.rooms_png == second.rooms_png


def test_open_outline_fails_loud() -> None:
    """外圈缺一整面墙即整张不出——外墙漏风的母版不许当几何唯一源往下游传。"""
    without_bottom = [wall for wall in _outer_walls() if wall.position_ratio != _BOTTOM]

    with pytest.raises(PlanMasterError, match="外圈没闭合"):
        render_plan_master(_geometry(walls=without_bottom))


def test_inner_opening_breaks_the_wall_but_outer_opening_stays_a_window() -> None:
    """内墙的洞是门（断开），外墙的洞是窗（墙不断）——外墙上的洞断开就把外圈拆了。"""
    door = PlanOpening(
        axis="vertical",
        position_ratio=_PARTITION_X,
        start_ratio=0.45,
        end_ratio=0.55,
        is_on_outer_wall=False,
    )
    window = PlanOpening(
        axis="horizontal",
        position_ratio=_TOP,
        start_ratio=0.35,
        end_ratio=0.65,
        is_on_outer_wall=True,
    )
    master = render_plan_master(_geometry(openings=[door, window]))
    walls = Image.open(io.BytesIO(master.walls_png))

    # 门那一段：隔墙上应当没有黑
    door_y = round((0.50 - _TOP) / (_BOTTOM - _TOP) * (master.height_px - 48)) + 24
    door_x = round((_PARTITION_X - _LEFT) / (_RIGHT - _LEFT) * (master.width_px - 48)) + 24
    assert _ink_at(walls, door_x, door_y) > 200, "内墙的洞应当把墙断开"

    # 窗那一段：外墙仍然连着——窗缝两侧还留着黑边
    window_x = round((0.50 - _LEFT) / (_RIGHT - _LEFT) * (master.width_px - 48)) + 24
    column = [_ink_at(walls, window_x, y) for y in range(60)]
    assert any(value < 100 for value in column), "外墙的洞应当画成窗，墙不断"

    # 窗仍然看得出是窗：墙心留了一条浅缝
    assert any(value > 200 for value in column[:60]), "窗缝没画出来，窗和实墙分不开"

    assert master.outline_closure_ratio >= 0.95


def test_geometry_without_walls_fails_loud() -> None:
    with pytest.raises(PlanMasterError, match="一段墙都没有"):
        render_plan_master(_geometry(walls=[]).model_copy(update={"walls": []}))


def _grid_walls_with_a_fat_right_edge() -> list[PlanWall]:
    """右外墙那一段网格投票投粗了一倍，其余与外轮廓对得上。

    造的是真实形态：网格投票**一条线只给一个厚度**，而外墙的厚度沿长度会变（角上那一截厚、
    中间那一长段薄），这个数于是只对其中一段成立。
    """
    return [
        wall.model_copy(update={"thickness_ratio": _THICKNESS * 2})
        if wall.axis == "vertical" and wall.position_ratio == _RIGHT
        else wall
        for wall in _outer_walls()
    ]


def test_outline_overrides_a_grid_wall_that_contradicts_it() -> None:
    """同一条外墙上两份描边差一倍时，母版按**外轮廓**那一份画，不叠出一条两倍宽的带子。

    来路：92㎡ 样例的次卧右外墙——业主收到图当场说"画得特别宽、像画了多层，中间还有条白缝"
    （2026-09-01）。那条带子是网格墙自己 22px 的厚度画出来的，外轮廓的 12px 整个在它里面；
    粗带子沿长度断开的地方只剩窄的那一笔，缝就是这么露出来的。
    """
    fat = render_plan_master(_geometry(walls=_grid_walls_with_a_fat_right_edge()))
    calibrated = render_plan_master(
        _geometry(walls=_grid_walls_with_a_fat_right_edge(), outline=_outer_walls())
    )

    fat_left_px, fat_right_px = _outer_wall_widths_px(fat.walls_png, 0.5)
    left_px, right_px = _outer_wall_widths_px(calibrated.walls_png, 0.5)

    # 右外墙压在图幅右缘上，外侧那半条被画布裁掉，量出来到不了整两倍——够说明"粗了一大截"
    assert fat_right_px > fat_left_px * 1.8, "样例没造出来：右外墙本该被网格墙画粗一大截"
    assert right_px == left_px, f"右外墙 {right_px}px 比左外墙 {left_px}px 宽——外轮廓没说了算"
    assert calibrated.outline_closure_ratio >= 0.95, "校准墙带不许把外圈量塌"


def test_outline_only_step_survives_the_calibration() -> None:
    """只有外轮廓描到的台阶（飘窗那种墙往外折一段）照旧画上。

    2026-08-31 补外轮廓解决的就是"网格投票表达不了这种台阶"；校准只换网格墙的墙带、
    **外轮廓一段不动**，所以那件事不会倒退。
    """
    step_x, right_edge = 0.83, 0.86
    step = PlanWall(
        axis="vertical",
        position_ratio=step_x,
        start_ratio=0.4,
        end_ratio=0.6,
        thickness_ratio=_THICKNESS,
    )
    master = render_plan_master(
        _geometry(
            walls=_grid_walls_with_a_fat_right_edge(),
            outline=[*_outer_walls(), step],
            plan_box=(_LEFT, _TOP, right_edge, _BOTTOM),
        )
    )
    walls = Image.open(io.BytesIO(master.walls_png))
    y = round((0.5 - _TOP) / (_BOTTOM - _TOP) * (master.height_px - 48)) + 24
    x = round((step_x - _LEFT) / (right_edge - _LEFT) * (master.width_px - 48)) + 24

    assert _ink_at(walls, x, y) < 128, "只在外轮廓里的那一段台阶没画上——补外轮廓那件事倒退了"


def test_outline_within_half_a_wall_leaves_the_grid_wall_alone() -> None:
    """两份描边差在半个墙厚以内是常态，一个坐标不动——校准只在两份互相矛盾时出手。"""
    nudged = [
        wall.model_copy(update={"position_ratio": wall.position_ratio + _THICKNESS * 0.4})
        if wall.axis == "vertical" and wall.position_ratio == _RIGHT
        else wall
        for wall in _outer_walls()
    ]
    master = render_plan_master(_geometry(outline=nudged))

    left_px, right_px = _outer_wall_widths_px(master.walls_png, 0.5)

    assert right_px > left_px, "两份只差一点点时该照旧取并集，不该改判成外轮廓那一份"
