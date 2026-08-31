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
    walls: list[PlanWall] | None = None, openings: list[PlanOpening] | None = None
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
        plan_box=(_LEFT, _TOP, _RIGHT, _BOTTOM),
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
