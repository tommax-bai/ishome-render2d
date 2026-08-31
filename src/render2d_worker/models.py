"""render2d_worker activity 出入参模型（pydantic）。

跨 domain 纪律：worker 不 import 其他 domain 的内部模块，activity 入参出参
以本模块与（后续）contracts 生成 SDK 为准。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PlanAxis = Literal["vertical", "horizontal"]


class _GeometryModel(BaseModel):
    """几何入参基类：camelCase 别名对齐产出侧的序列化；`extra=forbid` 拒收多出来的字段。

    **两个仓两种语言谁也不能 import 谁**，这一组模型是产出侧（几何提取）那份的逐字对面。
    对不上就是接不上头——同报告册对象键那条纪律。契约化的时点写死＝母版接进 activity 时，
    在那之前 CLI 直接吃产出侧的 JSON（先有消费方再有契约）。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class PlanWall(_GeometryModel):
    """一段墙：轴向、所在位置、起讫、墙厚，全部归一化到整图（0~1，左上角为原点）。

    `axis` 为 `vertical` 时 `position_ratio` 是 x、`start_ratio`/`end_ratio` 是 y 的起讫，
    **`thickness_ratio` 按图宽归一**；`horizontal` 时全部反过来（厚度按图高归一）。
    两个方向除的不是同一个数，所以还原形状必须用 :attr:`FloorplanGeometry.frame_width_px`。
    """

    axis: PlanAxis
    position_ratio: float
    start_ratio: float
    end_ratio: float
    thickness_ratio: float


class PlanOpening(_GeometryModel):
    """墙线上的一个洞：门、窗、或没有门扇的过口。坐标口径同 :class:`PlanWall`。

    **这一层不分门与窗**，只分洞在外墙还是内墙——母版因此把洞画成"墙断开"，不画门扇窗框。
    要画门扇与窗框得先有门窗之分，时点写死＝产出侧补上门窗识别那一批。
    """

    axis: PlanAxis
    position_ratio: float
    start_ratio: float
    end_ratio: float
    is_on_outer_wall: bool


class RoomOutline(_GeometryModel):
    """一个房间占的地方：若干矩形块拼起来（房间不一定是矩形，L 形很常见）。

    `boxes` 拼起来就是房间遮罩；`centroid` 是房间标注的锚点（质心算出来的，不是估的）。
    `area_ratio` 是占户型内部自由面积的比例，**不是面积**——要面积得先有比例尺，出图不需要。
    """

    name: str
    boxes: list[tuple[float, float, float, float]] = Field(default_factory=list)
    area_ratio: float = 0.0
    centroid: tuple[float, float] = (0.0, 0.0)


class FloorplanGeometry(_GeometryModel):
    """母版的唯一几何来源：轮廓、墙、洞、房间遮罩，**没有任何绝对尺寸**。

    `frame_*_px` 是这一整套比例的参照系（像素，不是真实世界尺寸）。缺了它画不出正确形状：
    x 按图宽归一、y 按图高归一，一张长方形的户型会被画成正方形。
    """

    frame_width_px: int
    frame_height_px: int
    plan_box: tuple[float, float, float, float]
    walls: list[PlanWall] = Field(default_factory=list)
    openings: list[PlanOpening] = Field(default_factory=list)
    rooms: list[RoomOutline] = Field(default_factory=list)
    cell_coverage_ratio: float = 0.0


class RoomAnchor(BaseModel):
    """房间在母版上的身份：遮罩里的索引 + 标注锚点的像素坐标。

    房间名**不画在母版上**——母版是几何唯一源，字是后面那一步的事（版面区文字零风险、
    房间标注要先过遮罩比对门禁）。母版只把"字该落在哪儿"算出来交出去。
    """

    name: str
    mask_index: int
    anchor_x_px: int
    anchor_y_px: int


class PlanMaster(BaseModel):
    """母版一次绘制的全部产物：给人看的那张 + 两张机器可读层 + 房间锚点。"""

    master_png: bytes
    walls_png: bytes
    rooms_png: bytes
    width_px: int
    height_px: int
    rooms: list[RoomAnchor] = Field(default_factory=list)
    outline_closure_ratio: float = 0.0
    """户型外轮廓被墙盖住的比例——母版自己的自证数，不达标整张不出（fail loud）。"""


class PlanRenderRequest(BaseModel):
    """plan-2d-render 输入。

    同一管线两种用途：确认底图（画 BaseFacts 识别结果）/ 母版（画冻结后的
    PreliminaryPlan）。除 PNG 外必须输出房间遮罩与墙体图层（机器可读层）。
    """

    revision_id: str
    purpose: Literal["confirmation_base", "plan_master"]
