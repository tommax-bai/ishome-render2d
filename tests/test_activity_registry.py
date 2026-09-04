"""activity 注册名与 contracts 注册表逐字一致的守门测试。

唯一真源：ishome-contracts `activities/registry.md`（只增不改）。本清单为其
副本；两处不一致时以 contracts 仓为准并回改此处。
"""

from __future__ import annotations

from temporalio import activity

from render2d_worker.activities import (
    ACTIVITY_PLAN_2D_RENDER,
    ACTIVITY_STYLE_CAPTION_OVERLAY,
    PlanRenderer,
    activity_registry,
)

# 注册名 → 函数名（kebab-case ↔ snake_case 动词前置，规范 §2.4）
CONTRACTS_ACTIVITY_REGISTRY: dict[str, str] = {
    "plan-2d-render": "render_plan_2d",
    # #18（contracts 2026-09-04 入册）：情绪图叠字，确定性绘制与母版同伸缩轴
    "style-caption-overlay": "overlay_style_caption",
}


def test_registry_matches_contracts() -> None:
    """本仓承接的注册名全集（contracts #4、#18），与 registries/task_queues.md 的
    render2d 行一致。"""
    assert {ACTIVITY_PLAN_2D_RENDER, ACTIVITY_STYLE_CAPTION_OVERLAY} == set(
        CONTRACTS_ACTIVITY_REGISTRY
    )
    assert set(activity_registry(PlanRenderer(object()))) == set(  # type: ignore[arg-type]
        CONTRACTS_ACTIVITY_REGISTRY
    )


def test_registered_temporal_names_match_the_keys() -> None:
    """常量、@activity.defn(name=...) 注册名、函数名三者一致。"""
    for name, fn in (
        (ACTIVITY_PLAN_2D_RENDER, PlanRenderer.render_plan_2d),
        (ACTIVITY_STYLE_CAPTION_OVERLAY, PlanRenderer.overlay_style_caption),
    ):
        defn = activity._Definition.from_callable(fn)  # noqa: SLF001
        assert defn is not None, f"{name} is not a temporal activity"
        assert defn.name == name
        assert fn.__name__ == CONTRACTS_ACTIVITY_REGISTRY[name]
