"""Temporal activity：`plan-2d-render`——母版与确定性图层绘制，产物写进私有对象存储。

注册名唯一真源：ishome-contracts `activities/registry.md` #4，**只增不改**——改注册名会破坏
历史 workflow 重放，等同于改线上协议；新增走 contracts 仓 PR 评审。
命名规则（规范 §2.4）：注册名 kebab-case 显式声明，函数名同词 snake_case 动词前置。

**绘图层至此成服务**（V1.4 裁决的形态本来就是"独立仓 + 独立 worker"，此前只有纯库 + CLI，
接进 activity 的时点写死＝派发链路接通时）。**CLI 不废**：改样式、看一张图长什么样仍走它，
不必起 Temporal、也不碰对象存储。两条路共用同一份纯库代码（`plan_master` / `plan_brief`），
分界由 import-linter 锁死（`cli` 看不见 `activities`）——不许出现两套画法。

射程按 contracts #4 逐字：**母版与确定性图层绘制——确认底图、功能说明图、风格图几何底图；
同时输出房间遮罩/墙体图层**。三种"图"是同一次绘制的三种用途，不是三条管线：
母版这一张既是给业主看的确认底图，也是 imagegen 图生图的几何底图。

纪律（与本层既有红线一致）：
- **确定性、零模型调用**：全程不调任何模型，同一份几何画两次逐字节相同；
- **几何不由 LLM 决定**：墙、洞、边界只从入参的几何来，这一层一个坐标都不发明；
- **画不出来就是这一步失败**：外圈闭合率不达标、批注挂不上房间、没有中文字体，一律整张不出，
  不给一张"差不多的"图——下游拿母版当几何唯一源；
- **写不进桶就不是 ok**：图画得再对、落不了地也按失败回报。回一个指向空气的键，
  下游会去签一条打不开的链接发给业主。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from temporalio import activity

from render2d_worker.cjk_font import CjkFontMissingError
from render2d_worker.models import FloorplanGeometry, PlanNote
from render2d_worker.plan_brief import PlanBriefError, render_plan_brief
from render2d_worker.plan_master import PlanMasterError, render_plan_master, room_anchors_json
from render2d_worker.plan_store import (
    BRIEF_ARTIFACT,
    MASTER_ARTIFACT,
    ROOM_ANCHORS_ARTIFACT,
    ROOMS_MASK_ARTIFACT,
    WALLS_ARTIFACT,
    OssPlanStore,
    PlanStoreError,
    content_sha256_of,
)

ACTIVITY_PLAN_2D_RENDER = "plan-2d-render"
"""contracts 注册名（#4）。字符串在此声明一次，worker 与守门测试都引它。"""


class PlanRenderer:
    """母版绘制 activity 的实现件，依赖由组合根（worker）注入。

    做成类而不是自由函数，是因为它要用一样**进程级**的东西：私有桶的连接。它该在**起进程时**
    装好并当场校验——缺凭证要在 worker 起不来的时候就知道，不是等第一张母版画完才发现存不进去。
    """

    def __init__(self, store: OssPlanStore) -> None:
        self._store = store

    @activity.defn(name=ACTIVITY_PLAN_2D_RENDER)
    async def render_plan_2d(self, request: dict[str, Any]) -> dict[str, Any]:
        """几何 → 母版 + 墙体图层 + 房间遮罩 + 房间锚点（+ 功能说明图）→ 写私有桶，返回对象键。

        入参是**不透明字典**而不是本仓模型：派发方（genpipe 编排）不 import 本仓存根签名，
        两边只靠 contracts 注册名接头（同报告渲染层那个 activity 的口径）。

        **几何内联传**：它是一份不大的 JSON，且今天几何还没有落桶的键——有键之后再改走键，
        触发条件写死＝几何产物进 contracts `registries/object_keys.md` 那一次。
        **图不内联**：一张 1600px 的母版塞进编排的返回值是拿 Temporal 当文件传输通道用，
        所以出参一律是对象键（见 `plan_store` 模块文档）。

        **不收 preview/final 两档**（registry 抬头对"涉渲染的 activity"有这条）：母版是几何
        唯一源，下游所有风格图回读它、"户型有没有漂"靠它量——一个低质档的母版会让遮罩比对
        整条失效。要分档的时点写死＝出现"母版本身要出快慢两版"的消费方时。
        """
        floorplan_object_key = str(request.get("floorplan_object_key") or "")
        if not floorplan_object_key:
            return _failed(
                "gate-missing-floorplan-key",
                "没有 floorplan_object_key：产物的键与源户型图同前缀派生，无从落地",
            )
        try:
            # 先验键再画图：键不成立时画完再发现等于白画一次，还差点把图写到别人的前缀底下
            content_sha256_of(floorplan_object_key)
        except PlanStoreError as e:
            return _violations("gate-bad-floorplan-key", e.details)

        try:
            geometry = FloorplanGeometry.model_validate(request.get("geometry"))
        except (ValueError, TypeError) as e:
            return _failed("gate-bad-geometry", f"几何解析失败：{e}")
        try:
            notes = [PlanNote.model_validate(note) for note in request.get("notes") or []]
        except (ValueError, TypeError) as e:
            return _failed("gate-bad-notes", f"批注解析失败：{e}")

        try:
            master = render_plan_master(geometry)
        except PlanMasterError as e:
            return _violations("plan-master-failed", e.details)

        # 批注是模型在别处产的（引得到事实、过了机检），这一层只负责把它画到该在的位置上。
        # 没给批注就只出母版那一批——确认底图那一步本来就还没有批注可画。
        brief_png: bytes | None = None
        if notes:
            try:
                brief_png = render_plan_brief(master, notes).image_png
            except (PlanBriefError, CjkFontMissingError) as e:
                return _violations("plan-brief-failed", e.details)

        artifacts: list[tuple[str, bytes]] = [
            (MASTER_ARTIFACT, master.master_png),
            (WALLS_ARTIFACT, master.walls_png),
            (ROOMS_MASK_ARTIFACT, master.rooms_png),
            (ROOM_ANCHORS_ARTIFACT, room_anchors_json(master.rooms)),
        ]
        if brief_png is not None:
            artifacts.append((BRIEF_ARTIFACT, brief_png))
        try:
            keys = {
                artifact: self._store.put_artifact(floorplan_object_key, artifact, payload)
                for artifact, payload in artifacts
            }
        except PlanStoreError as e:
            return _violations("plan-store-failed", e.details)

        return {
            "verdict": "ok",
            "bucket": self._store.bucket_name,
            "master_key": keys[MASTER_ARTIFACT],
            "walls_key": keys[WALLS_ARTIFACT],
            "rooms_mask_key": keys[ROOMS_MASK_ARTIFACT],
            "room_anchors_key": keys[ROOM_ANCHORS_ARTIFACT],
            # 没给批注就没有说明图，键给 None 而不是省掉这个键：读到 None 是"这次没画"，
            # 读不到键是"这个 activity 换了形态"，两件事不能长成一个样子。
            "brief_key": keys.get(BRIEF_ARTIFACT),
            "width_px": master.width_px,
            "height_px": master.height_px,
            # 母版自己的自证数带出来：下游要回答"这张底图有多可信"时不必再算一遍
            "outline_closure_ratio": master.outline_closure_ratio,
            "room_count": len(master.rooms),
            "note_count": len(notes),
        }


def _failed(check: str, detail: str) -> dict[str, Any]:
    return {"verdict": "failed", "violations": [{"check": check, "detail": detail}]}


def _violations(check: str, details: list[str]) -> dict[str, Any]:
    """逐条回报违规——不空替、不静默跳过，也不把几条并成一句人读不懂的话。"""
    return {
        "verdict": "failed",
        "violations": [{"check": check, "detail": detail} for detail in details],
    }


def activity_registry(renderer: PlanRenderer) -> dict[str, Callable[..., Any]]:
    """本仓承接的 activity 全集（队列 `render2d-activities`）。

    键与 contracts 注册表逐字一致（tests/test_activity_registry.py 断言）。
    """
    return {ACTIVITY_PLAN_2D_RENDER: renderer.render_plan_2d}
