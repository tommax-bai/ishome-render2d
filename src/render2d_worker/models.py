"""render2d_worker activity 出入参模型（pydantic）。

跨 domain 纪律：worker 不 import 其他 domain 的内部模块，activity 入参出参
以本模块与（后续）contracts 生成 SDK 为准。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PlanRenderRequest(BaseModel):
    """plan-2d-render 输入。

    同一管线两种用途：确认底图（画 BaseFacts 识别结果）/ 母版（画冻结后的
    PreliminaryPlan）。除 PNG 外必须输出房间遮罩与墙体图层（机器可读层）。
    """

    revision_id: str
    purpose: Literal["confirmation_base", "plan_master"]
