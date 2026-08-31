"""母版 activity：正路径、五条失败路径，以及"写不进桶不许当成功"。

**存储用桩件不用真桶**：这一层要验的是"图画出来之后往哪走、走不通怎么办"，
真桶验的是凭证与网络对不对——那件事由真跑存档留档，不是单测的题目。

几何按**产出侧序列化的样子**（camelCase）写：activity 的入参是不透明字典，
两边只靠 contracts 注册名接头，那么单测就该喂它真实会收到的那份 JSON，而不是本仓模型。
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from PIL import Image

from render2d_worker.activities import ACTIVITY_PLAN_2D_RENDER, PlanRenderer, activity_registry
from render2d_worker.plan_store import (
    BRIEF_ARTIFACT,
    MASTER_ARTIFACT,
    PLAN_ARTIFACT_KEY_TEMPLATE,
    ROOM_ANCHORS_ARTIFACT,
    ROOMS_MASK_ARTIFACT,
    UPLOAD_ORIGINAL_KEY_TEMPLATE,
    WALLS_ARTIFACT,
    PlanStoreError,
    content_sha256_of,
    plan_artifact_key_of,
)

_SHA256 = "e2a41bfefa2488a157e2335baeb5255306bb43b0c0f014fc86e729e9b4fea514"
"""真跑那张户型图的内容哈希（楼书 92㎡ 三室）——键就是从这个数派生的。"""

_FLOORPLAN_KEY = f"uploads/{_SHA256}/original.png"

_LEFT, _TOP, _RIGHT, _BOTTOM = 0.2, 0.2, 0.8, 0.8
_PARTITION_X = 0.5
_THICKNESS = 0.01


def _wall(axis: str, position: float, start: float, end: float) -> dict[str, Any]:
    return {
        "axis": axis,
        "positionRatio": position,
        "startRatio": start,
        "endRatio": end,
        "thicknessRatio": _THICKNESS,
    }


def _geometry() -> dict[str, Any]:
    """两间房、中间一道隔墙的方户型，外圈闭合（同 test_plan_master 的那一户）。"""
    return {
        "frameWidthPx": 1000,
        "frameHeightPx": 1000,
        "planBox": [_LEFT, _TOP, _RIGHT, _BOTTOM],
        "walls": [
            _wall("vertical", _LEFT, _TOP, _BOTTOM),
            _wall("vertical", _RIGHT, _TOP, _BOTTOM),
            _wall("horizontal", _TOP, _LEFT, _RIGHT),
            _wall("horizontal", _BOTTOM, _LEFT, _RIGHT),
            _wall("vertical", _PARTITION_X, _TOP, _BOTTOM),
        ],
        "openings": [],
        "rooms": [
            {
                "name": "客厅",
                "boxes": [[_LEFT, _TOP, _PARTITION_X, _BOTTOM]],
                "areaRatio": 0.5,
                "centroid": [0.35, 0.5],
            },
            {
                "name": "主卧",
                "boxes": [[_PARTITION_X, _TOP, _RIGHT, _BOTTOM]],
                "areaRatio": 0.5,
                "centroid": [0.65, 0.5],
            },
        ],
        "cellCoverageRatio": 0.98,
    }


_NOTES = [
    {"room": "客厅", "text": "外墙上没窗户，白天要开灯", "cites": ["plan-daylight-客厅"]},
]


class _StubPlanStore:
    """桩件私有桶：记下写了什么，或按需当场失败。"""

    def __init__(self, *, fail_with: str | None = None) -> None:
        self.written: dict[str, bytes] = {}
        self._fail_with = fail_with

    @property
    def bucket_name(self) -> str:
        return "ishome-test"

    def put_artifact(self, floorplan_object_key: str, artifact: str, payload: bytes) -> str:
        if self._fail_with is not None:
            raise PlanStoreError([self._fail_with])
        key = plan_artifact_key_of(floorplan_object_key, artifact)
        self.written[key] = payload
        return key


def _renderer(store: Any) -> PlanRenderer:
    return PlanRenderer(store)


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "floorplan_object_key": _FLOORPLAN_KEY,
        "geometry": _geometry(),
    }
    request.update(overrides)
    return request


async def test_renders_and_writes_every_artifact() -> None:
    store = _StubPlanStore()
    result = await _renderer(store).render_plan_2d(_request(notes=_NOTES))

    assert result["verdict"] == "ok"
    assert result["master_key"] == f"uploads/{_SHA256}/{MASTER_ARTIFACT}"
    assert result["walls_key"] == f"uploads/{_SHA256}/{WALLS_ARTIFACT}"
    assert result["rooms_mask_key"] == f"uploads/{_SHA256}/{ROOMS_MASK_ARTIFACT}"
    assert result["room_anchors_key"] == f"uploads/{_SHA256}/{ROOM_ANCHORS_ARTIFACT}"
    assert result["brief_key"] == f"uploads/{_SHA256}/{BRIEF_ARTIFACT}"
    assert result["room_count"] == 2
    assert result["note_count"] == 1
    assert result["outline_closure_ratio"] >= 0.90

    # 回报的每一条键都真落了字节，不是一个指向空气的键
    assert set(store.written) == {
        result["master_key"],
        result["walls_key"],
        result["rooms_mask_key"],
        result["room_anchors_key"],
        result["brief_key"],
    }
    assert store.written[result["master_key"]].startswith(b"\x89PNG")
    anchors = json.loads(store.written[result["room_anchors_key"]])
    assert [room["name"] for room in anchors] == ["客厅", "主卧"]
    # 说明图是母版加了批注面板的那一张：比母版高
    with Image.open(io.BytesIO(store.written[result["brief_key"]])) as brief:
        assert brief.height > result["height_px"]


async def test_no_notes_still_renders_the_master_batch() -> None:
    """确认底图那一步本来就还没有批注可画——没批注只是没说明图，不是失败。"""
    store = _StubPlanStore()
    result = await _renderer(store).render_plan_2d(_request())

    assert result["verdict"] == "ok"
    assert result["brief_key"] is None
    assert result["note_count"] == 0
    assert len(store.written) == 4


async def test_same_geometry_writes_byte_identical_artifacts() -> None:
    """确定性是这一层的红线不是优点：接进 activity 之后仍是同一份纯库代码在画。"""
    first, second = _StubPlanStore(), _StubPlanStore()
    await _renderer(first).render_plan_2d(_request(notes=_NOTES))
    await _renderer(second).render_plan_2d(_request(notes=_NOTES))

    assert first.written == second.written


async def test_store_failure_is_not_reported_as_success() -> None:
    """写不进去就是这一步没成。图画得再对，落不了地也不是 ok。"""
    store = _StubPlanStore(fail_with="桶不存在")
    result = await _renderer(store).render_plan_2d(_request())

    assert result["verdict"] == "failed"
    assert [v["check"] for v in result["violations"]] == ["plan-store-failed"]
    assert "桶不存在" in result["violations"][0]["detail"]


async def test_open_outline_fails_loud_and_writes_nothing() -> None:
    """外圈闭合率那道门禁接进 activity 之后照旧生效——画不出来就是这一步失败。"""
    geometry = _geometry()
    geometry["walls"] = [
        wall
        for wall in geometry["walls"]
        if not (wall["axis"] == "horizontal" and wall["positionRatio"] == _BOTTOM)
    ]
    store = _StubPlanStore()
    result = await _renderer(store).render_plan_2d(_request(geometry=geometry))

    assert result["verdict"] == "failed"
    assert [v["check"] for v in result["violations"]] == ["plan-master-failed"]
    assert "外圈没闭合" in result["violations"][0]["detail"]
    assert store.written == {}


async def test_note_on_a_room_the_master_does_not_have_fails_loud() -> None:
    """挂不上去就整张不出，不悄悄丢掉——丢掉之后图上少一条，没有人会发现。"""
    store = _StubPlanStore()
    stray = [{"room": "书房", "text": "安静", "cites": ["plan-share-书房"]}]
    result = await _renderer(store).render_plan_2d(_request(notes=stray))

    assert result["verdict"] == "failed"
    assert [v["check"] for v in result["violations"]] == ["plan-brief-failed"]
    assert "书房" in result["violations"][0]["detail"]
    # 母版画出来了也一件都不写：这一次派发是失败的，不留半批产物在桶里
    assert store.written == {}


@pytest.mark.parametrize(
    ("overrides", "expected_check"),
    [
        ({"floorplan_object_key": ""}, "gate-missing-floorplan-key"),
        ({"floorplan_object_key": "uploads/../../etc/passwd"}, "gate-bad-floorplan-key"),
        ({"geometry": {"planBox": [0, 0, 1, 1]}}, "gate-bad-geometry"),
        ({"notes": [{"room": "客厅"}]}, "gate-bad-notes"),
    ],
)
async def test_bad_input_fails_loud(overrides: dict[str, Any], expected_check: str) -> None:
    """入参不成立时说清是哪一条不成立，不画半张也不写半批。"""
    store = _StubPlanStore()
    result = await _renderer(store).render_plan_2d(_request(**overrides))

    assert result["verdict"] == "failed"
    assert [v["check"] for v in result["violations"]] == [expected_check]
    assert store.written == {}


def test_activity_registry_exposes_the_contracts_name() -> None:
    result = activity_registry(_renderer(_StubPlanStore()))

    assert list(result) == [ACTIVITY_PLAN_2D_RENDER]


def test_object_keys_are_derived_not_recorded() -> None:
    """产物键由源图的内容哈希推得——问存储即知，不必另立一张台账。

    源图键模板的唯一真源在 contracts `registries/object_keys.md`，本行是逐字副本；
    产物键模板是本轮的默认（入表时点见 `plan_store` 模块文档）。两处对不上就是接不上头。
    """
    assert UPLOAD_ORIGINAL_KEY_TEMPLATE == "uploads/{content_sha256}/original.{ext}"
    assert PLAN_ARTIFACT_KEY_TEMPLATE == "uploads/{content_sha256}/{artifact}"
    assert content_sha256_of(_FLOORPLAN_KEY) == _SHA256
    assert (
        plan_artifact_key_of(_FLOORPLAN_KEY, MASTER_ARTIFACT)
        == f"uploads/{_SHA256}/plan-master.png"
    )
    # 同一张户型图重跑覆盖同一批对象，天然幂等
    assert plan_artifact_key_of(_FLOORPLAN_KEY, MASTER_ARTIFACT) == plan_artifact_key_of(
        _FLOORPLAN_KEY, MASTER_ARTIFACT
    )


async def test_worker_says_in_one_line_why_it_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """起不来的原因要**一眼读得懂**：缺配置是运维看的一句话，不是给开发看的调用栈。

    这条盯的是形态不是文案——缺凭证时抛 SystemExit 带一句人话并**点名缺的是哪一个**，
    而不是让 PlanStoreError 带着调用栈冒到终端。缺配置在部署现场是最常见的一种"起不来"。
    """
    from render2d_worker import worker

    # 这四个名字是与运维之间的部署契约（凭证放 ~/.ishome/oss-local.env），逐字写在这儿
    for name in (
        "ISHOME_OSS_ENDPOINT",
        "ISHOME_OSS_BUCKET_PRIVATE",
        "ISHOME_OSS_ACCESS_KEY_ID",
        "ISHOME_OSS_ACCESS_KEY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as failure:
        # 桶装不上就起不来，连 Temporal 都不去连——不带着半套配置上线等第一张图去踩
        await worker.run_worker("localhost:7233")
    assert "ISHOME_OSS_ENDPOINT" in str(failure.value)
    assert "oss-local.env" in str(failure.value)


@pytest.mark.parametrize(
    "key",
    [
        "uploads/e2a41bfe/original.png",  # 哈希不够长
        f"uploads/{_SHA256}/original.tiff",  # 格式不在闭集里
        f"uploads/{_SHA256.upper()}/original.png",  # 大写十六进制不是同一条键
        f"reports/{_SHA256}/original.png",  # 别人的前缀
        f"uploads/{_SHA256}/plan-master.png",  # 拿产物键当源图键递进来
    ],
)
def test_a_key_that_is_not_an_upload_fails_loud(key: str) -> None:
    """键错一次图就写到别人的前缀底下去了——认死形态不放宽。"""
    with pytest.raises(PlanStoreError, match="不成立"):
        content_sha256_of(key)
