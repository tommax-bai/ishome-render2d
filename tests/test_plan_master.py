"""母版红线：外圈不闭合不出图、同一份几何画两次逐字节相同、洞按内外墙分两种画法。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from render2d_worker.models import (
    FloorplanGeometry,
    PlanOpening,
    PlanWall,
    PlanWallBand,
    RoomOutline,
)
from render2d_worker.plan_master import PlanMasterError, render_plan_master, room_anchors_json

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


def _column_ink_runs_px(image: Image.Image, x: int, until_y: int) -> list[tuple[int, int]]:
    """一列上从顶往下数黑段：逐段给 (起点 y, 高度 px)。量窗缝居不居中就是量这个。"""
    runs: list[tuple[int, int]] = []
    y = 0
    while y < until_y:
        if _ink_at(image, x, y) < 128:
            start = y
            while y < until_y and _ink_at(image, x, y) < 128:
                y += 1
            runs.append((start, y - start))
        else:
            y += 1
    return runs


def test_window_slit_stays_centered_in_the_wall_band_actually_laid() -> None:
    """窗缝开在**实际砌出来的墙带**正中——洞自带的中心线与墙带中心不一致时跟墙带走。

    来路：92㎡ 样例的卫生间窗（2026-09-01）。洞的位置继承自网格墙的中心线，墙带却按
    外轮廓砌在别的中心上，窗缝仍按洞的中心擦，一侧黑边剩 3px、另一侧 15px——业主看到的
    "两条细竖线夹白缝"。
    """
    nudged_outline = [
        PlanWall(
            axis="horizontal",
            position_ratio=_TOP + _THICKNESS * 0.4,
            start_ratio=_LEFT,
            end_ratio=_RIGHT,
            thickness_ratio=_THICKNESS,
        )
    ]
    window = PlanOpening(
        axis="horizontal",
        position_ratio=_TOP,
        start_ratio=0.35,
        end_ratio=0.45,
        is_on_outer_wall=True,
    )
    master = render_plan_master(_geometry(openings=[window], outline=nudged_outline))
    walls = Image.open(io.BytesIO(master.walls_png))

    window_x = round((0.40 - _LEFT) / (_RIGHT - _LEFT) * (master.width_px - 48)) + 24
    edges = _column_ink_runs_px(walls, window_x, 80)

    assert len(edges) == 2, f"窗该是两条黑边夹一条缝，量到 {edges}"
    (_, first_px), (_, second_px) = edges
    assert abs(first_px - second_px) <= 2, (
        f"窗缝没开在墙带正中：两侧黑边 {first_px}px 与 {second_px}px——"
        f"缝跟着洞自带的中心线跑了，没跟着实际砌的墙带"
    )
    assert min(first_px, second_px) >= 6, f"有一侧黑边细成了发丝：{edges}"


def test_a_quantization_seam_between_colinear_strokes_is_bridged() -> None:
    """同一条线上相邻两段描边之间 1 源图像素的量化缝要补住，不许印成白发丝。

    来路：92㎡ 样例阳台左下（2026-09-01）。外轮廓逐段描、段的起讫落在源图整像素上，
    相邻段之间留 1 源图像素的缝；两段中心又错着位，谁都不盖这道缝，印出来是
    "带白色缺口的黑块堆叠"。

    接缝故意不放在 0.5：隔墙在那儿，那一列自带墨，缝没补上测试也会假绿。
    """
    seam_at = 0.35
    split_top = [
        PlanWall(
            axis="horizontal",
            position_ratio=_TOP,
            start_ratio=_LEFT,
            end_ratio=seam_at,
            thickness_ratio=_THICKNESS,
        ),
        PlanWall(
            axis="horizontal",
            position_ratio=_TOP + _THICKNESS * 0.4,
            start_ratio=seam_at + 0.001,  # 1 源图像素的缝（图宽 1000px）
            end_ratio=_RIGHT,
            thickness_ratio=_THICKNESS,
        ),
        *(
            wall
            for wall in _outer_walls()
            if wall.axis == "vertical" or wall.position_ratio != _TOP
        ),
    ]
    master = render_plan_master(_geometry(walls=split_top))
    walls = Image.open(io.BytesIO(master.walls_png))

    seam_x = round((seam_at + 0.0005 - _LEFT) / (_RIGHT - _LEFT) * (master.width_px - 48)) + 24
    seam_column = [_ink_at(walls, seam_x, y) for y in range(80)]

    assert any(value < 128 for value in seam_column), (
        "两段描边接缝处的那一列全是白的——量化缝没补上，印出来就是一条白发丝"
    )


def test_a_step_between_parallel_strokes_is_not_bridged() -> None:
    """平行而不共线的两段之间一像素也不补——那是台阶（飘窗那种），不是接缝。"""
    step_gap_at = 0.5
    right_edge = 0.86
    steps = [
        PlanWall(
            axis="vertical",
            position_ratio=0.83,
            start_ratio=0.3,
            end_ratio=step_gap_at,
            thickness_ratio=_THICKNESS,
        ),
        PlanWall(
            axis="vertical",
            position_ratio=0.845,
            start_ratio=step_gap_at + 0.001,
            end_ratio=0.7,
            thickness_ratio=_THICKNESS,
        ),
    ]
    master = render_plan_master(
        _geometry(outline=steps, plan_box=(_LEFT, _TOP, right_edge, _BOTTOM))
    )
    walls = Image.open(io.BytesIO(master.walls_png))

    # 空当带：两段 run 的端点之间、横跨两条线的墙带并集——补了任何一笔墨都会被抓到
    scale = (master.width_px - 48) / ((right_edge - _LEFT) * _FRAME[0])
    y_lo = int((step_gap_at * _FRAME[1] - _TOP * _FRAME[1]) * scale + 24) + 1
    y_hi = int(((step_gap_at + 0.001) * _FRAME[1] - _TOP * _FRAME[1]) * scale + 24) - 1
    x_lo = int(((0.83 - _THICKNESS / 2 - _LEFT) * _FRAME[0]) * scale + 24)
    x_hi = int(((0.845 + _THICKNESS / 2 - _LEFT) * _FRAME[0]) * scale + 24) + 1
    assert y_lo <= y_hi, "样例没造出空当：两段 run 的端点之间连一行都不剩"
    stray_ink = [
        (x, y)
        for y in range(y_lo, y_hi + 1)
        for x in range(x_lo, x_hi + 1)
        if _ink_at(walls, x, y) < 128
    ]
    assert not stray_ink, (
        f"台阶空当里被补了墨 {stray_ink[:4]}——不共线的两段之间不是接缝，补墨就是造墙"
    )


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


def _middle_run_px(walls_png: bytes, y_ratio: float) -> tuple[int, int]:
    """某一行上左右外墙之间那道墙带：(起点 x, 宽度 px)。量按段砌出来的隔墙就是量这个。"""
    walls = Image.open(io.BytesIO(walls_png))
    y = round((y_ratio - _TOP) / (_BOTTOM - _TOP) * (walls.height - 48)) + 24
    runs = _ink_runs_px(walls, y)
    assert len(runs) == 3, f"这一行上该有左外墙、隔墙、右外墙三段：{runs}"
    return runs[1]


def test_banded_wall_lays_each_measured_band_with_the_still_face() -> None:
    """按段厚度直接砌：每段照实测的两面画，突变处**数值没动的那一面**在图上也不动。

    次卧右外墙（角上 22px、其余 12px，东面对齐）与玄关交界（12px 收成 6px，西面对齐）
    都是这个形态——位置加厚度的还原会把面对齐画丢，所以砌法必须吃两面。
    """
    bands = [
        PlanWallBand(start_ratio=_TOP, end_ratio=0.5, face_low_ratio=0.495, face_high_ratio=0.505),
        PlanWallBand(
            start_ratio=0.5, end_ratio=_BOTTOM, face_low_ratio=0.485, face_high_ratio=0.505
        ),
    ]
    base = _geometry()
    walls = [
        wall.model_copy(update={"bands": bands})
        if wall.axis == "vertical" and wall.position_ratio == _PARTITION_X
        else wall
        for wall in base.walls
    ]
    master = render_plan_master(base.model_copy(update={"walls": walls}))

    upper_x, upper_w = _middle_run_px(master.walls_png, 0.35)
    lower_x, lower_w = _middle_run_px(master.walls_png, 0.65)

    assert lower_w == pytest.approx(upper_w * 2, abs=2), "下半段实测厚一倍，砌出来就该厚一倍"
    assert upper_x + upper_w == lower_x + lower_w, "高位面（东面）对齐：加厚的量全该鼓向西面"
    assert upper_x != lower_x, "西面该动而没动——两段画成同一条带子，段厚没被吃进来"


def test_measured_band_trims_a_borrowed_outline_thickness_where_they_overlap() -> None:
    """外轮廓借来的厚度描在**量得到**的墙上时，重叠的那部分改用实测段带，其余原样。

    来路：玄关入口内凹边（2026-09-01）——外轮廓量不到墙像素、借全图中位数当厚度，
    把实测 6px 的墙描成 12px，两侧各多出一条黑边。段带够不着的地方（门洞那截）
    照旧按借来的厚度画：那儿兜底仍是唯一来路，裁掉等于把户型画漏。
    """
    borrowed = PlanWall(
        axis="vertical",
        position_ratio=_PARTITION_X,
        start_ratio=_TOP,
        end_ratio=_BOTTOM,
        thickness_ratio=0.03,
    )
    measured = PlanWall(
        axis="vertical",
        position_ratio=_PARTITION_X,
        start_ratio=0.4,
        end_ratio=0.6,
        thickness_ratio=0.01,
        bands=[
            PlanWallBand(
                start_ratio=0.4, end_ratio=0.6, face_low_ratio=0.495, face_high_ratio=0.505
            )
        ],
    )
    master = render_plan_master(
        _geometry(walls=[*_outer_walls(), measured], outline=[*_outer_walls(), borrowed])
    )

    _, trimmed_w = _middle_run_px(master.walls_png, 0.5)
    _, borrowed_w = _middle_run_px(master.walls_png, 0.3)

    assert trimmed_w <= 30, f"段带盖到的那截还是 {trimmed_w}px 宽——借来的厚度没让位给实测"
    assert borrowed_w >= 70, f"段带够不着的那截只剩 {borrowed_w}px——把兜底也裁掉了，户型画漏"


_PINNED_DISPATCH_SHA256 = {
    "plan-master.png": "17ff6be3e3097e9691bed3d93d203bd1fa07d3cc26e667453bb81456c6bc81df",
    "plan-walls.png": "884edddf16f0d34a29da25681b6e2ab05b63dc09e17cca2322967d4e5177a562",
    "plan-rooms-mask.png": "8ad59cd6149feb59dca19245f9a3ec67027814fbbd2e72a08f550570c2a347a2",
    "plan-rooms.json": "2e9e82160d3d115487503dde8ab07775ad48c7f5bb4d85ec24813e74142242b0",
}
"""无段厚的真派发在按段厚度改造当天（2026-09-01，Pillow 版本由 uv.lock 钉住）的产物哈希。
Pillow 升级会挪 PNG 编码器的字节——那时要**有意识地**重钉，而不是顺手改数。"""


def test_bandless_dispatch_renders_byte_identical_to_the_pinned_run() -> None:
    """旧派发（无段厚数据）渲染逐字节不变——按段厚度只许改带段的数据，兜底路径一根线不动。

    吃的是 `_iteration/` 里 2026-08-31 真跑的派发原件（正文冻结的日志附件，不是测试造的样本）：
    "网格/外轮廓校准 + 接缝桥接"那整套兜底逻辑的行为由这四个哈希钉死。
    """
    import hashlib
    import json
    from pathlib import Path

    dispatch = json.loads(
        (
            Path(__file__).parent.parent
            / "_iteration/run-2026-08-31-plan-2d-render-to-oss/dispatch.json"
        ).read_text(encoding="utf-8")
    )
    master = render_plan_master(FloorplanGeometry.model_validate(dispatch["geometry"]))

    produced = {
        "plan-master.png": master.master_png,
        "plan-walls.png": master.walls_png,
        "plan-rooms-mask.png": master.rooms_png,
        "plan-rooms.json": room_anchors_json(master.rooms),
    }
    for artifact, pinned in _PINNED_DISPATCH_SHA256.items():
        assert hashlib.sha256(produced[artifact]).hexdigest() == pinned, (
            f"{artifact} 与钉住的字节不同——无段厚兜底路径被动到了"
        )
