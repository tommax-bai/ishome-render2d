"""中文字体解析：按一张写死的清单找，**找不到就响亮失败**。

母版不写字是刻意的（它因此不依赖字体，本机与服务器画出来逐字节相同）；到功能说明图这一步
躲不过了——房间名与批注都是中文。

**为什么不退成默认字体**：Pillow 的内置位图字体没有中文字形，退给它得到的是一整张豆腐块，
而那是一张**看起来画出来了**的图。本机有字体、服务器没有，于是本机好看、部上去全是方块——
这正是"静默降级"最典型的形态：失败发生在离现场最远的地方。所以这里只有两种结果：
写得出中文，或者一张图都不出。

**服务器上现在一个中文字体都没有**（2026-08-31 实测：`fc-list :lang=zh` 为空，只有
DejaVu/Liberation 几套西文）。装法写死＝`dnf install google-noto-sans-cjk-ttc-fonts`
（仓里现成），**触发条件＝render2d 上服务器那一次**——现在装是为还没部署的服务先装，不做。
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

CJK_FONT_CANDIDATES = (
    # 服务器（Alibaba Cloud Linux，装 google-noto-sans-cjk-ttc-fonts 之后）
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    # 本机（macOS）
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
)
"""按顺序找。**清单是写死的不是搜出来的**——搜到哪个用哪个，等于让图的样子随机器变。"""

_PROBE = "厨房阳台"
"""拿它验字体真认得中文：文件存在、装得进 Pillow，都不等于有中文字形。"""


class CjkFontMissingError(Exception):
    """本机没有中文字体。响亮失败，不画豆腐块。"""

    def __init__(self, details: list[str]) -> None:
        super().__init__("；".join(details))
        self.details = details


def find_cjk_font(
    size_px: int, candidates: tuple[str, ...] = CJK_FONT_CANDIDATES
) -> ImageFont.FreeTypeFont:
    """按清单取一个认得中文的字体。一个都不认即抛。"""
    for path in candidates:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, size_px)
        except OSError:
            continue
        if _writes_cjk(font):
            return font
    raise CjkFontMissingError(
        [
            "找不到认得中文的字体，功能说明图不出（不画豆腐块）",
            "找过：" + "、".join(candidates),
            "服务器上装：dnf install google-noto-sans-cjk-ttc-fonts",
        ]
    )


def _writes_cjk(font: ImageFont.FreeTypeFont) -> bool:
    """这个字体真写得出中文吗：量一下探针字串，宽度为零就是没有字形。"""
    try:
        left, _, right, _ = font.getbbox(_PROBE)
    except OSError:
        return False
    return right - left > 0
