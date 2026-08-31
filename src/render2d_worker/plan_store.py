"""出站边缘：把母版这一批产物写进**私有对象存储**（阿里云 OSS 私有桶，用户裁决 2026-08-30 晚）。

**为什么走对象存储而不是把图塞进编排的返回值**：下游吃这批图的是另外的服务——风格图归
imagegen（另一个仓、另一台机器的伸缩轴），功能说明图要由 channel-svc 发到业主手机上。
一张 1600px 的母版塞进 Temporal 的 payload 是拿编排当文件传输通道用；而私有桶的签名链接
由 OSS 域名直接对外、自带有效期，本项目一个公网端口都不用开（同报告册那条线的落法）。

**本模块只写不签**。签名是"给谁看、看多久"的事，属业务侧——生成侧不知用户是谁。
两边靠**确定性对象键**接头：产物键与源户型图**同前缀**，见下方模板。

依赖方向（import-linter 锁定）：本模块只依赖运行库（oss2），不感知上层——写图的那只手
看不见图上画了什么。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import oss2

UPLOAD_ORIGINAL_KEY_TEMPLATE = "uploads/{content_sha256}/original.{ext}"
"""源户型图（用户发来的那一份）的对象键模板。

**唯一真源在 contracts `registries/object_keys.md`**，本行是逐字副本——写的一侧是 channel-svc
（Java，收图即落桶），读的一侧是生成侧（Python），两个仓两种语言谁也不能 import 谁，
只能靠同一条键接头。对不上就是接不上头，不是风格问题（守门测试盯住）。
"""

UPLOAD_EXTENSIONS = ("jpg", "png", "webp", "gif", "bmp")
"""`{ext}` 的闭集，同上逐字副本。由**字节首部**判定，不按渠道给的文件名猜（contracts 原文）。"""

PLAN_ARTIFACT_KEY_TEMPLATE = "uploads/{content_sha256}/{artifact}"
"""母版这一批产物的对象键模板：**与源图同前缀**。

这条形态是本轮的默认，**入 contracts 注册表的时点写死＝中控仓那侧统一改
`registries/object_keys.md` 那一次**（本仓不动那个文件）。它对得上注册表开头写的三条理由：

- **确定性派生**：键由源图的内容哈希推得，跑两遍写同一个对象，天然幂等；
- **不铸新流水号**：不引入 ULID——重推一次就多一个没人认领的对象；
- **键里没有身份与渠道方言**：生成侧不知用户是谁，而键是生成侧算出来的。

注册表原文给同一份上传件的派生物在 `uploads/{content_sha256}/` 下留了位置，这批就落在那儿。
"""

MASTER_ARTIFACT = "plan-master.png"
"""母版：给人看的那一张，也是风格图的几何底图（imagegen 图生图吃它）。"""

WALLS_ARTIFACT = "plan-walls.png"
"""墙体图层：白纸黑墙、不带房间底色，几何跟随度与配准吃这一张。"""

ROOMS_MASK_ARTIFACT = "plan-rooms-mask.png"
"""房间遮罩索引图：像素值＝房间序号。逐房间比对重合度吃这一张。"""

ROOM_ANCHORS_ARTIFACT = "plan-rooms.json"
"""房间锚点清单：房间名 + 遮罩序号 + 标注锚点像素坐标。风格图的房间表由它填。"""

BRIEF_ARTIFACT = "plan-brief.png"
"""功能说明图：母版 + 房间名 + 批注，发给业主的成品。"""

_CONTENT_TYPE_BY_SUFFIX = {
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}
"""写进去时就得写对：签名链接不改这个头，写错了业主点开看到的就不是一张图。"""

_UPLOAD_ORIGINAL_KEY_PATTERN = re.compile(
    r"^uploads/(?P<content_sha256>[0-9a-f]{64})/original\.(?:" + "|".join(UPLOAD_EXTENSIONS) + r")$"
)
"""源图键的形态。**认死这一条不放宽**：产物键由它派生，键错一次图就写到别人的前缀底下去了。"""

_ENDPOINT_ENV = "ISHOME_OSS_ENDPOINT"
_BUCKET_ENV = "ISHOME_OSS_BUCKET_PRIVATE"
_ACCESS_KEY_ID_ENV = "ISHOME_OSS_ACCESS_KEY_ID"
_ACCESS_KEY_SECRET_ENV = "ISHOME_OSS_ACCESS_KEY_SECRET"


class PlanStoreError(Exception):
    """图写不进去——响亮失败。回一个指向空气的键，下游会去签一条打不开的链接。"""

    def __init__(self, details: list[str]) -> None:
        super().__init__("；".join(details))
        self.details = details


def content_sha256_of(floorplan_object_key: str) -> str:
    """从源图的对象键取出内容哈希。形态不对即抛——不猜、不修补、不换个地方写。"""
    matched = _UPLOAD_ORIGINAL_KEY_PATTERN.match(floorplan_object_key)
    if matched is None:
        raise PlanStoreError(
            [
                f"源户型图的对象键不成立：`{floorplan_object_key}`——"
                f"要的是 {UPLOAD_ORIGINAL_KEY_TEMPLATE}"
                f"（{{content_sha256}} 是 64 位小写十六进制，"
                f"{{ext}} ∈ {'/'.join(UPLOAD_EXTENSIONS)}）"
            ]
        )
    return matched.group("content_sha256")


def plan_artifact_key_of(floorplan_object_key: str, artifact: str) -> str:
    """一件产物的对象键：与源图同前缀。确定性派生，同一张户型图重跑覆盖同一批对象。"""
    return PLAN_ARTIFACT_KEY_TEMPLATE.format(
        content_sha256=content_sha256_of(floorplan_object_key), artifact=artifact
    )


@dataclass(frozen=True)
class OssSettings:
    """私有桶连接口径。四个值全部来自环境，代码里不留任何默认桶名或端点。"""

    endpoint: str
    bucket: str
    access_key_id: str
    access_key_secret: str

    @staticmethod
    def from_env() -> OssSettings:
        """从环境读取；**缺一即启动就失败**，不等到第一张母版画完才发现存不进去。"""
        values = {
            name: os.environ.get(name, "").strip()
            for name in (_ENDPOINT_ENV, _BUCKET_ENV, _ACCESS_KEY_ID_ENV, _ACCESS_KEY_SECRET_ENV)
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise PlanStoreError(
                [
                    f"私有对象存储没配全，缺：{'、'.join(missing)}——凭证放"
                    " ~/.ishome/oss-local.env（本机）或 /opt/ishome/env/oss.env（服务器），不入库"
                ]
            )
        return OssSettings(
            endpoint=values[_ENDPOINT_ENV],
            bucket=values[_BUCKET_ENV],
            access_key_id=values[_ACCESS_KEY_ID_ENV],
            access_key_secret=values[_ACCESS_KEY_SECRET_ENV],
        )


class OssPlanStore:
    """阿里云 OSS 私有桶的产物写入口。签名不在这里——本层只写不签（见模块文档）。"""

    def __init__(self, settings: OssSettings) -> None:
        auth = oss2.Auth(settings.access_key_id, settings.access_key_secret)
        self._bucket = oss2.Bucket(auth, settings.endpoint, settings.bucket)
        self._bucket_name = settings.bucket

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    def put_artifact(self, floorplan_object_key: str, artifact: str, payload: bytes) -> str:
        """写一件产物，返回对象键。写失败即上抛——不吞、不返回一个指向空气的键。"""
        key = plan_artifact_key_of(floorplan_object_key, artifact)
        suffix = artifact[artifact.rfind(".") :]
        content_type = _CONTENT_TYPE_BY_SUFFIX.get(suffix)
        if content_type is None:
            raise PlanStoreError([f"不认识的产物类型 `{artifact}`：写进去的 Content-Type 说不出"])
        try:
            self._bucket.put_object(key, payload, headers={"Content-Type": content_type})
        except oss2.exceptions.OssError as e:
            raise PlanStoreError([f"图写不进私有桶 `{self._bucket_name}`（键 {key}）：{e}"]) from e
        return key
