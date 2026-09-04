"""情绪图叠字 activity：取键 → 叠字 → 写回派生键；放不下就失败、桶取不到就失败。

存储用桩件（同 test_activities）；叠字判据本身在 test_style_caption，这里只验这一层的
"从哪取、往哪写、什么情况不许当成功"。
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image, ImageDraw

from render2d_worker.activities import PlanRenderer
from render2d_worker.plan_store import CAPTIONED_SUFFIX, PlanStoreError, captioned_key_of

_SHA = "e2a41bfefa2488a157e2335baeb5255306bb43b0c0f014fc86e729e9b4fea514"
_STYLE_KEY = f"uploads/{_SHA}/atmosphere-cream-journal.jpg"
_COPY: dict[str, Any] = {
    "title": "暖光小院",
    "summary": "推开家门，每个角落都自然舒展",
    "tips": ["玄关放个换鞋凳", "阳台选细腿家具", "厨卫都有窗更清爽"],
}


def _page(top_blank_share: float, bottom_blank_share: float) -> bytes:
    width, height = 900, 1400
    page = Image.new("RGB", (width, height), (247, 244, 238))
    pen = ImageDraw.Draw(page)
    top = int(height * top_blank_share)
    bottom = height - int(height * bottom_blank_share)
    for y in range(top, bottom, 3):
        pen.line([(0, y), (width, y)], fill=(10, 10, 10), width=2)
    buffer = io.BytesIO()
    page.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


class _StubStore:
    def __init__(self, objects: dict[str, bytes], *, fail_put: bool = False) -> None:
        self.objects = objects
        self.written: dict[str, bytes] = {}
        self._fail_put = fail_put

    @property
    def bucket_name(self) -> str:
        return "ishome-test"

    def get_upload_object(self, key: str) -> bytes:
        if key not in self.objects:
            raise PlanStoreError([f"桶里没有 {key}"])
        return self.objects[key]

    def put_captioned_visual(self, style_object_key: str, payload: bytes) -> str:
        if self._fail_put:
            raise PlanStoreError(["写不进去"])
        key = captioned_key_of(style_object_key)
        self.written[key] = payload
        return key


def test_captioned_key_is_derived_from_the_style_key() -> None:
    assert (
        captioned_key_of(_STYLE_KEY) == f"uploads/{_SHA}/atmosphere-cream-journal{CAPTIONED_SUFFIX}"
    )
    with pytest.raises(PlanStoreError):
        captioned_key_of("reports/x/book.html")
    with pytest.raises(PlanStoreError):
        captioned_key_of(f"uploads/{_SHA}/noext")


async def test_overlay_writes_a_png_under_the_derived_key() -> None:
    store = _StubStore({_STYLE_KEY: _page(0.28, 0.30)})
    result = await PlanRenderer(store).overlay_style_caption(  # type: ignore[arg-type]
        {"style_object_key": _STYLE_KEY, "copy": _COPY}
    )

    assert result["verdict"] == "ok", result
    assert result["image_object_key"] == captioned_key_of(_STYLE_KEY)
    assert result["content_type"] == "image/png"
    written = store.written[result["image_object_key"]]
    assert written.startswith(b"\x89PNG"), "叠字成品由确定性绘制层重新编码为 PNG"
    with Image.open(io.BytesIO(written)) as out:
        assert out.size == (result["width_px"], result["height_px"]) == (900, 1400)


async def test_no_room_for_text_is_a_failure_not_a_squashed_image() -> None:
    store = _StubStore({_STYLE_KEY: _page(0.01, 0.30)})
    result = await PlanRenderer(store).overlay_style_caption(  # type: ignore[arg-type]
        {"style_object_key": _STYLE_KEY, "copy": _COPY}
    )
    assert result["verdict"] == "failed"
    assert result["violations"][0]["check"] == "style-caption-failed"
    assert store.written == {}


async def test_missing_style_image_in_bucket_fails_loud() -> None:
    result = await PlanRenderer(_StubStore({})).overlay_style_caption(  # type: ignore[arg-type]
        {"style_object_key": _STYLE_KEY, "copy": _COPY}
    )
    assert result["violations"][0]["check"] == "plan-store-failed"


@pytest.mark.parametrize(
    ("request_body", "expected_check"),
    [
        ({"copy": _COPY}, "gate-missing-style-key"),
        ({"style_object_key": _STYLE_KEY, "copy": {"title": "x"}}, "gate-bad-copy"),
        (
            {"style_object_key": _STYLE_KEY, "copy": {"title": " ", "summary": "s", "tips": ["t"]}},
            "gate-empty-copy",
        ),
    ],
)
async def test_bad_input_fails_before_touching_the_bucket(
    request_body: dict[str, Any], expected_check: str
) -> None:
    store = _StubStore({_STYLE_KEY: _page(0.28, 0.30)})
    result = await PlanRenderer(store).overlay_style_caption(request_body)  # type: ignore[arg-type]
    assert result["violations"][0]["check"] == expected_check
    assert store.written == {}


async def test_store_write_failure_is_not_reported_as_success() -> None:
    store = _StubStore({_STYLE_KEY: _page(0.28, 0.30)}, fail_put=True)
    result = await PlanRenderer(store).overlay_style_caption(  # type: ignore[arg-type]
        {"style_object_key": _STYLE_KEY, "copy": _COPY}
    )
    assert result["verdict"] == "failed"
    assert result["violations"][0]["check"] == "plan-store-failed"
