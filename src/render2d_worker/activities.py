"""Temporal activities：所有 IO 与重计算收口在此。

注册名唯一真源：ishome-contracts `activities/registry.md`，**只增不改**——改注册名
会破坏历史 workflow 重放，等同于改线上协议；新增走 contracts 仓 PR 评审。
命名规则（规范 §2.4）：注册名 = kebab-case 显式声明；函数名 = 同词 snake_case
动词前置。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from temporalio import activity

from render2d_worker.models import PlanRenderRequest

ActivityResult = dict[str, Any]


@activity.defn(name="plan-2d-render")
async def render_plan_2d(request: PlanRenderRequest) -> ActivityResult:
    """母版与确定性图层绘制：确认底图/母版同一管线；输出 PNG + 房间遮罩 + 墙体图层。"""
    raise NotImplementedError


ACTIVITY_REGISTRY: dict[str, Callable[..., Coroutine[Any, Any, ActivityResult]]] = {
    "plan-2d-render": render_plan_2d,
}
"""注册名 → 实现。键与 contracts 注册表逐字一致（tests/test_activity_registry.py 断言）。"""
