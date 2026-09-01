"""说明图红线：批注挂不上去就不画、房间名画在锚点上、同样输入画两次逐字节相同。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from render2d_worker.cjk_font import CJK_FONT_CANDIDATES, CjkFontMissingError, find_cjk_font
from render2d_worker.models import PlanMaster, PlanNote, RoomAnchor
from render2d_worker.plan_brief import (
    BRIEF_STYLE_BLUEPRINT,
    BRIEF_STYLE_DEFAULT,
    BRIEF_STYLE_PRECISION,
    BRIEF_STYLE_TECH,
    PlanBriefError,
    render_plan_brief,
)


def _master() -> PlanMaster:
    blank = Image.new("L", (400, 500), 236)
    buffer = io.BytesIO()
    blank.save(buffer, format="PNG")
    png = buffer.getvalue()
    return PlanMaster(
        master_png=png,
        walls_png=png,
        rooms_png=png,
        width_px=400,
        height_px=500,
        rooms=[
            RoomAnchor(name="客厅", mask_index=1, anchor_x_px=200, anchor_y_px=300),
            RoomAnchor(name="阳台", mask_index=2, anchor_x_px=200, anchor_y_px=450),
        ],
        outline_closure_ratio=0.97,
    )


_NOTES = [
    PlanNote(room="客厅", text="外墙上没窗户，白天要开灯", cites=["plan-daylight-客厅"]),
    PlanNote(room="阳台", text="特别狭长，晾衣杆得贴边装", cites=["plan-shape-阳台"]),
]


def test_brief_renders_plan_and_notes() -> None:
    brief = render_plan_brief(_master(), _NOTES)

    assert brief.note_count == 2
    # 底图原样在上面，批注排在它下面——画布比母版高
    assert brief.width_px == 400
    assert brief.height_px > 500


def test_same_input_renders_byte_identical() -> None:
    master, notes = _master(), _NOTES

    assert render_plan_brief(master, notes).image_png == render_plan_brief(master, notes).image_png


def test_note_on_a_room_the_master_does_not_have_fails_loud() -> None:
    """挂不上去就不画，不悄悄丢掉——丢掉之后图上少一条，没有人会发现。"""
    stray = [PlanNote(room="书房", text="安静", cites=["plan-share-书房"])]

    with pytest.raises(PlanBriefError, match="书房"):
        render_plan_brief(_master(), stray)


def test_no_notes_is_not_a_brief() -> None:
    with pytest.raises(PlanBriefError, match="一条批注都没有"):
        render_plan_brief(_master(), [])


def test_default_style_row_is_pinned_to_the_shipped_look() -> None:
    """默认那一行钉死在历来的观感上：白纸、236 灰房、纯黑墙、26 灰字、不铺纹不发光。

    样式表引入时的口径（2026-09-01）：**变体是给业主拍板用的样张，默认输出一个字节不变**。
    默认行的值一动，发给业主那条路就变了——那要走拍板，不走顺手改。
    """
    style = BRIEF_STYLE_DEFAULT

    assert style.paper_rgb == (255, 255, 255)
    assert style.room_zone_rgbs == ((236, 236, 236),)
    assert style.wall_ink_rgb == (0, 0, 0)
    assert style.wall_core_rgb is None
    assert style.grid_step_px is None
    assert style.wall_glow_rgb is None
    assert style.label_ink_rgb == (26, 26, 26)
    assert style.label_halo_rgb == (255, 255, 255)
    assert style.note_ink_rgb == (26, 26, 26)


def test_not_passing_a_style_is_the_default_row() -> None:
    master, notes = _master(), _NOTES

    assert (
        render_plan_brief(master, notes).image_png
        == render_plan_brief(master, notes, BRIEF_STYLE_DEFAULT).image_png
    )


def test_style_variants_change_the_look_but_not_the_information() -> None:
    """变体只换观感不减信息：版面尺寸、批注数与默认一致，字节与默认不同。"""
    master, notes = _master(), _NOTES
    default = render_plan_brief(master, notes)

    for style in (BRIEF_STYLE_BLUEPRINT, BRIEF_STYLE_PRECISION, BRIEF_STYLE_TECH):
        styled = render_plan_brief(master, notes, style)
        assert styled.image_png != default.image_png, f"{style.name} 画出来和默认一个样"
        assert (styled.width_px, styled.height_px) == (default.width_px, default.height_px)
        assert styled.note_count == default.note_count


def test_missing_cjk_font_fails_loud_instead_of_drawing_tofu() -> None:
    """本机有字体、服务器没有，于是本机好看、部上去全是方块——静默降级最典型的形态。

    所以只有两种结果：写得出中文，或者一张图都不出。
    """
    with pytest.raises(CjkFontMissingError, match="不画豆腐块"):
        find_cjk_font(20, candidates=("/nowhere/NoSuchFont.ttc",))


def test_font_candidates_cover_both_the_laptop_and_the_server() -> None:
    # 服务器 2026-08-31 实测原本一个中文字体都没有，装的是 google-noto-sans-cjk-ttc-fonts
    assert any("google-noto-cjk" in path for path in CJK_FONT_CANDIDATES)
    assert any(path.startswith("/System/Library/Fonts") for path in CJK_FONT_CANDIDATES)
