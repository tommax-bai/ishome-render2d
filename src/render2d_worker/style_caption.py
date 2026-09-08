"""给风格图叠字：标题、情绪总结、三块小贴士。

**版面自己造，不向模型要**（用户裁决 2026-09-07："把'留白'从'求模型'改成'自己做'。
生成侧不再要求留白带"）。做法是：把模型画的那张画面**原样**放到一张更高的纸上，
上下白边的高度由文案排完之后的实测高度算出来——**100% 放得下，零重试**。

**为什么改**：此前这一层是"叠字前先量版面，放不下就整张失败去重生成"，而留白是在
imagegen 模板里用一句英文向模型讨的（`cream-journal` 的 `RESERVE BLANK PAPER…`）——
**模型给多少是它的事**。2026-09-07 真机两跑照出代价：一跑三次全判不过、业主一张图没收到；
另一跑第三次才过，7 分 26 秒里 7 分钟耗在"生图 → 判不过 → 重生"这个循环上。
把确定性的排版约束交给概率性的生成模型、再用重试去赌，赌一把两分钟加一次 2K 生图的钱。

**画面一个像素都不动**：不缩放、不拉伸、不裁切，原尺寸贴上去——那是业主家的户型，
变形就是画错了。成图宽度＝画面宽度（画面左右照旧出血到页边，读起来是"图还往外延伸"，
而不是"被裁了一刀"）；成图高度＝画面高度＋上下两条白边，**比例随文案长短变**，
不去凑一个固定比例——凑比例就得缩画面，那是拿"不变形"换"好看的数字"。

**纸接得上**：白边的底色取画面**那一侧边缘**的中位色（不是整张一个色——真图实测底边比
顶边暗，是画的落影），再把画面里一段纯纸的纸纹镜像铺到接缝处、往外淡出。
纯色的效果实测过：放大三倍看接缝那一线由"有颗粒"突变成"死平"，底边还多一道 7 级的色阶；
铺上纸纹后同样放大三倍看不出接缝，也看不出重复（92㎡ 真图，2026-09-07）。

**折行按中文排版规则来，不按字宽硬切**（2026-09-08）：先按句读断成短句、短句当整块装行，
断不开才按字断，再由行首禁则/行尾禁则/孤字规则收拾断点（`_wrap`）。硬切那一版把
`绿植` 断成 `绿` / `植`，是 138㎡ 真跑样张照出来的。**"不在词内断"只做到句读这一层**——
再往里要分词，分词要引词典依赖且本身也是猜，停在这儿（`_by_char` 的注释写了触发条件）。

**为什么这一层在 render2d 不在 imagegen**：确定性绘制归这个仓——字要落得准，坐标不能是猜的。
生成式那边只管画面、一个字不写；两边分工与"母版不写字"是同一条线。
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import cast

from PIL import Image, ImageDraw, ImageFont, ImageOps

from render2d_worker.cjk_font import find_cjk_font
from render2d_worker.models import PlanCopy, StyleCaptioned

BLANK_ROW_TOLERANCE = 60
"""一行算不算空白：行内明暗跨度不超过它就算。

**这个数是量出来的不是拍的**：同一张真跑图上，留白带里的行跨度中位 20（纸纹颗粒），
画面带里中位 237——差一个数量级，40/60/80 三个取值给出完全相同的判定，说明判据落在分离带上
而不是骑在边界上。首版取 12，卡在纸纹上，把一张留了白的图判成一点白都没留。

**它现在的用途变了**：不再拿来判"够不够放字"（版面自己造，不用判），而是拿来在画面里
**找一段纯纸**当纸纹样本，以及验边缘那一条取样带确实是纸、不是压着家具。"""

TEXT_PAD_SHARE = 0.25
"""字块**上下各**留这么多余量（占字块高的比例）——免得字贴着纸边或画面边缘。

白边高度＝字块高 ×(1 + 2×本值)。数没变，口径按这行字给足了：从前只加一份，
因为那时字是往一段现成的空白里居中放，余量不是纸边距；现在白边是自己造的，
外侧那一份余量就是纸边距，少给一份就成了字贴着纸边。"""

GRAIN_FADE_MIRROR_CYCLES = 2
"""纸纹从接缝往外铺几个镜像周期，铺完就淡尽、只剩纯色。

镜像平铺的周期＝纸纹段高的两倍，铺两个周期即 4 倍段高。**这个倍数有真图**：92㎡ 那张的
纯纸段实测 29 行，铺 116px 淡出，放大三倍看不出接缝也看不出重复（纯色版在同样放大下
接缝可见）。倍数跟着段高走而不是写一个绝对像素数，是因为段短的时候本来就只该在接缝
附近借一点纹理——铺远了就是把一条纹路当一片纸用。"""

_INK = (32, 30, 28)
_SUBTLE = (96, 92, 88)

_TEXT_WIDTH_SHARE = 0.80
"""标题与总结占页宽这么多；小贴士按 0.82 占自己那一栏（见 `render_caption`）。折行按字数折，
折完再按字体实测宽度复核——中日韩字形一格一个字宽，西文更窄，实测那一道是防外语标题。"""

_CLAUSE_ENDERS = "，。、；：？！…,.;:?!"
"""断句处：一个短句到这儿算完，标点留在它自己那句的尾巴上。

**中文没有词间空格，句读是唯一不用分词就能确定的语义边界**——断在这儿一定不在词中间。
真文案实测（138㎡ 那三条小贴士，2026-09-07）：14/13/15 字，每条正好一个逗号，一栏 11 字，
按句读断出来是 6+8、6+7、8+7，两行都比按字硬切（11+3、11+2、11+4）匀。"""

_NO_LINE_START = "，。、；：？！…—）〕】》」』〉”’,.;:?!)]}"
"""行首禁则：句读与收尾的括号引号不许领头一行。中文排版的基本规则，纯确定性可做。

半角那几个一起收进来，是因为文案由模型产出，`，` 与 `,` 都可能出现（真跑见过混用）。"""

_NO_LINE_END = "（〔【《「『〈“‘([{"
"""行尾禁则：起头的括号引号不许留在行尾——它得跟着它领的那段走。"""

_ORPHAN_TAIL_CHARS = 2
"""末行少到这个字数（含）就算孤字，要往回调断点匀一行。

**这个数是排版规则本身的定义，不是量出来的**（区别于 `BLANK_ROW_TOLERANCE` 那种实测阈值，
也不是一条裁决）：孤字的说法就是"末行只剩一两个字"，一两个字＝ ≤2。
写成常量是为了让判据露在外面，不是为了看起来有依据。"""


class StyleCaptionError(Exception):
    """字叠不上去。响亮失败——**宁可整张不出，不把字压在他家客厅上**。

    **改成自己造版面之后它理论上不该再触发**：白边高度由文案算出来，多长的文案都放得下。
    还能触发的只剩"图本身不成立"那一类（窄到一个字都放不下一行）。真触发了说明别处有问题，
    **不许静默兜底**——这一层宁可什么都不出。
    """

    def __init__(self, details: list[str]) -> None:
        super().__init__("；".join(details))
        self.details = details


def _patch_side_px(image: Image.Image) -> int:
    """取样块的边长：短边的 1/40，不小于 8px。四角纸色与边缘纸色共用这一个尺度。"""
    return max(8, min(image.width, image.height) // 40)


def _median_rgb(image: Image.Image) -> tuple[int, int, int]:
    """一块图的中位色（逐通道取中位）。

    **中位不是均值**：得有一半以上的像素是画才拉得动它——取样块里蹭进一角家具、一条墙线，
    纸色都不跟着跑。均值不行，一条黑墙线就把整块拉暗。
    """
    channels = []
    for band in image.convert("RGB").split():
        values = sorted(cast("Sequence[int]", list(band.get_flattened_data())))
        channels.append(values[len(values) // 2])
    return (channels[0], channels[1], channels[2])


def _paper_colour(image: Image.Image) -> tuple[int, int, int]:
    """这张纸本身什么颜色：四角各取一小块的中位色，再逐通道取这四个色的中位。

    纸色因风格而异（奶油粉的纸与彩铅扫描稿的纸不是一个颜色），所以不写死一个数。
    **四取中位而不是四取均值**：生成侧不再留白之后，画面铺满整幅、某个角压着阳台或飘窗
    是常态（92㎡ 真图的左下角就压着阳台），四个里坏两个也还是纸色。
    """
    patch = _patch_side_px(image)
    corners = [
        (0, 0),
        (image.width - patch, 0),
        (0, image.height - patch),
        (image.width - patch, image.height - patch),
    ]
    samples = [_median_rgb(image.crop((x, y, x + patch, y + patch))) for x, y in corners]
    middles = [sorted(sample[channel] for sample in samples) for channel in range(3)]
    return cast("tuple[int, int, int]", tuple((values[1] + values[2]) // 2 for values in middles))


def _grey_of(colour: tuple[int, int, int]) -> int:
    """RGB → 灰（同 Pillow 转 `L` 的 ITU-R 601-2 系数）。判"这一行是不是纸"用得着。"""
    red, green, blue = colour
    return (red * 299 + green * 587 + blue * 114) // 1000


def _blank_run(image: Image.Image, paper_grey: int) -> tuple[int, int]:
    """整张图里最长的一段连续空白行，返回（起始行, 行数）。这一段就是纸纹样本的取处。

    一行算空白要同时满足两条：**行内明暗跨度小**，且**它跟纸一样亮**。
    只看跨度不够——**一整行纯黑的跨度也是 0**，会被当成空白；单测造图时一条贯穿全宽的黑线
    就把首版骗过去了。两条一起才是"这一行上什么都没画"。
    """
    grey = image.convert("L")
    pixels = cast("Sequence[int]", list(grey.get_flattened_data()))
    width = image.width
    best_start, best_len, run_start, run_len = 0, 0, 0, 0
    for index, start in enumerate(range(0, len(pixels), width)):
        row = pixels[start : start + width]
        if (
            row
            and max(row) - min(row) <= BLANK_ROW_TOLERANCE
            and min(row) >= paper_grey - BLANK_ROW_TOLERANCE
        ):
            run_start = run_start if run_len else index
            run_len += 1
            if run_len > best_len:
                best_start, best_len = run_start, run_len
        else:
            run_len = 0
    return best_start, best_len


def _shifted(texture: Image.Image, by: tuple[int, int, int]) -> Image.Image:
    """把一块纸纹整体挪到另一个色调上：逐通道加一个常数，保留颗粒、只换底色。

    纸纹样本取自画面里某一段纯纸，而白边的底色取的是**画面那一侧边缘**的色——两处差几级
    （真图实测底边比顶边暗 7 级，是画的落影）。直接贴样本会在接缝上留一道色阶，
    所以贴的是"样本减样本中位色再加白边底色"。
    """
    bands = [
        band.point(lambda value, offset=offset: max(0, min(255, value + offset)))
        for band, offset in zip(texture.convert("RGB").split(), by, strict=True)
    ]
    return Image.merge("RGB", bands)


def _paper_band(
    art: Image.Image,
    height_px: int,
    *,
    paper: tuple[int, int, int],
    grain: Image.Image | None,
) -> Image.Image:
    """在画面**上方**接出一条 `height_px` 高的纸，底边贴着画面第 0 行。

    要在下方接：把画面上下翻过来求一次，再把结果翻回去（`render_caption` 就是这么做的）——
    同一段代码不写两遍，也就不会出现"上边接得上、下边接不上"这种两套画法的差异。
    """
    if height_px <= 0:
        return Image.new("RGB", (art.width, 0))
    # 白边的底色取画面**这一侧边缘**那一条的中位色：接缝两边同色，才没有色阶。
    # 取不到纸（这一侧整条压着画面）就退回整张的纸色——宁可有一点色差，不要接出一条黑边。
    edge = _median_rgb(art.crop((0, 0, art.width, min(_patch_side_px(art), art.height))))
    if abs(_grey_of(edge) - _grey_of(paper)) > BLANK_ROW_TOLERANCE:
        edge = paper

    band = Image.new("RGB", (art.width, height_px), edge)
    if grain is None:
        return band

    fade_px = min(height_px, grain.height * 2 * GRAIN_FADE_MIRROR_CYCLES)
    texture = Image.new("RGB", (art.width, fade_px))
    grain_median = _median_rgb(grain)
    tile = _shifted(
        grain, (edge[0] - grain_median[0], edge[1] - grain_median[1], edge[2] - grain_median[2])
    )
    # 从贴着画面的那一侧往外铺，逐块镜像翻转——两块之间不留硬接缝
    filled, flip = 0, False
    while filled < fade_px:
        piece = ImageOps.flip(tile) if flip else tile
        take = min(piece.height, fade_px - filled)
        texture.paste(
            piece.crop((0, piece.height - take, piece.width, piece.height)),
            (0, fade_px - filled - take),
        )
        filled += take
        flip = not flip
    # 越靠近接缝纸纹越足，往外线性淡尽——淡出的是颗粒的强弱，看不出边界
    column = Image.new("L", (1, fade_px))
    for row in range(fade_px):
        column.putpixel((0, row), round(255 * row / (fade_px - 1)) if fade_px > 1 else 255)
    band.paste(
        texture,
        (0, height_px - fade_px),
        column.resize((art.width, fade_px), Image.Resampling.NEAREST),
    )
    return band


def _clauses(text: str) -> list[str]:
    """按句读把一段话切成短句，标点跟在它所属那句的尾巴上（所以切完天然不违行首禁则）。

    连着的标点（`……`、`？！`）算同一个句尾，不切成两句。
    """
    out: list[str] = []
    head = 0
    for at, char in enumerate(text):
        tail_of_run = at + 1 == len(text) or text[at + 1] not in _CLAUSE_ENDERS
        if char in _CLAUSE_ENDERS and tail_of_run:
            out.append(text[head : at + 1])
            head = at + 1
    if head < len(text):
        out.append(text[head:])
    return out


def _break_at(text: str, per_line: int) -> int:
    """在 `text` 里挑断点：从满一行处**往回退**，退到两条禁则都满足为止。

    退（本行少一个字）不是进（把标点塞进本行）：进会让这一行宽过栏，而横向放不下是要整张
    不出的（`StyleCaptionError`）——禁则不该反过来把版面撑破。退到只剩一个字就停手，
    免得一串标点长过一栏时退成空行、死循环。
    """
    cut = min(per_line, len(text))
    while cut > 1 and (
        (cut < len(text) and text[cut] in _NO_LINE_START) or text[cut - 1] in _NO_LINE_END
    ):
        cut -= 1
    return cut


def _unorphan(lines: list[str], per_line: int) -> list[str]:
    """末行只剩一两个字就把最后两行匀一匀——孤零零吊一个字在那儿最难看。

    只匀最后两行、只在按字断那一路用：句读断出来的行本身是完整短句，再去匀它等于拿"不孤字"
    换"断在词里"，方向反了。匀不动（匀完宽过一栏）就原样返回，不硬凑。
    """
    if len(lines) < 2 or len(lines[-1]) > _ORPHAN_TAIL_CHARS:
        return lines
    merged = lines[-2] + lines[-1]
    cut = _break_at(merged, (len(merged) + 1) // 2)
    if len(merged) - cut > per_line:
        return lines
    return [*lines[:-2], merged[:cut], merged[cut:]]


def _by_char(text: str, per_line: int) -> list[str]:
    """一个短句本身就宽过一栏，只能按字断：禁则兜住标点，末行太短再匀一次。

    **这一路会断在词中间**：判词界要分词，中文分词要引第三方词典依赖，这个仓今天只依赖
    Pillow（见本轮记录里为什么不引）。走到这一路的前提是"一个不带句读的短句就宽过一栏"——
    真文案的三条小贴士都不走这儿。
    """
    lines: list[str] = []
    rest = text
    while len(rest) > per_line:
        cut = _break_at(rest, per_line)
        lines.append(rest[:cut])
        rest = rest[cut:]
    lines.append(rest)
    return _unorphan(lines, per_line)


def _wrap(text: str, per_line: int) -> list[str]:
    """折行：**先按句读断，断不开才按字断**。

    从前是按字宽硬切，`阳台细长但通透，种点绿植刚刚好` 被切成 `…种点绿` / `植刚刚好`——
    `绿植` 拆成两行（138㎡ 真跑样张，2026-09-07）。中文没有词间空格，硬切不知道词在哪儿；
    但**句读知道语义单位在哪儿**，且它是文案自带的、不用引分词依赖。所以：把短句当成不可拆的
    整块贪心装行，装不下的那一块才落到按字断，再由禁则与孤字规则收拾断点。
    """
    lines: list[str] = []
    for clause in _clauses(text):
        if lines and len(lines[-1]) + len(clause) <= per_line:
            lines[-1] += clause
        elif len(clause) <= per_line:
            lines.append(clause)
        else:
            lines.extend(_by_char(clause, per_line))
    return lines or [""]


def _ink_center(font: ImageFont.FreeTypeFont, line: str) -> float:
    """这一行**墨迹**的中点（相对行首）。行尾不带标点时它跟字宽的中点差不到 1px。

    **为什么不直接按字宽居中**：中日韩标点是全角格子，`，` 的墨只占左下角三分之一，
    右边三分之二是空的。按句读折行之后行尾常带一个逗号，按字宽居中就把这一行整体推左——
    138㎡ 真图实测偏 16px（小贴士字号 43，合 0.37 个字宽），同一条贴士的两行肉眼就不对齐了
    （第二行同样实测偏 0.5px，即"不带标点的行本来是准的"）。
    """
    mask = font.getmask(line)
    box = mask.getbbox() if mask.size[0] else None
    return (box[0] + box[2]) / 2 if box else font.getlength(line) / 2


def _centered(
    pen: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    center_x: int,
    top_y: int,
    line_px: int,
    fill: tuple[int, int, int],
) -> int:
    for line in lines:
        # 锚点从 `ma`（按字宽居中）改成 `la` + 自己算的墨心：纵向基准仍是 ascender，不变
        pen.text(
            (round(center_x - _ink_center(font, line)), top_y),
            line,
            font=font,
            fill=fill,
            anchor="la",
        )
        top_y += line_px
    return top_y


def render_caption(style_png: bytes, copy: PlanCopy) -> StyleCaptioned:
    """风格图 + 文案 → 成品：画面原样居中，上下白边由文案实测高度算出来，字落在白边里。"""
    with Image.open(io.BytesIO(style_png)) as source:
        art = source.convert("RGB")

    width = art.width
    title_px = max(int(width * 0.062), 24)
    summary_px = max(int(width * 0.028), 14)
    tip_px = max(int(width * 0.023), 12)
    title_line, summary_line, tip_line = (
        int(title_px * 1.25),
        int(summary_px * 1.5),
        int(tip_px * 1.5),
    )
    title_font, summary_font, tip_font = (
        find_cjk_font(title_px),
        find_cjk_font(summary_px),
        find_cjk_font(tip_px),
    )

    # **标题也折行**：从前它是一行画上去的，长标题会顶出页边被裁掉；版面既然是自己造的，
    # 折行之后白边跟着长高，不存在"放不下"这回事。
    title_lines = _wrap(copy.title, max(1, int(width * _TEXT_WIDTH_SHARE) // title_px))
    summary_lines = _wrap(copy.summary, max(1, int(width * _TEXT_WIDTH_SHARE) // summary_px))
    column = width // max(len(copy.tips), 1)
    tip_lines = [_wrap(tip, max(1, int(column * 0.82) // tip_px)) for tip in copy.tips]

    over_wide = [
        f"这一行画出来 {int(font.getlength(line))}px，宽过 {limit}px 的栏：{line}"
        for lines, font, limit in (
            (title_lines, title_font, width),
            (summary_lines, summary_font, width),
            *((lines, tip_font, column) for lines in tip_lines),
        )
        for line in lines
        if font.getlength(line) > limit
    ]
    if over_wide:
        # 白边高度是算出来的，纵向永远够；能到这儿只剩"图窄到一个字都放不下一行"这一类。
        raise StyleCaptionError(
            ["版面横向放不下这些字——图本身不成立，整张不出，不裁字也不缩字", *over_wide]
        )

    # 白边＝字块实测高 + 上下各一份余量。**没有一个拍出来的高度**：文案长白边就高，
    # 短就矮；`TEXT_PAD_SHARE` 按它自己的口径算"上下各留"，这一版之前只加了一份，
    # 从前那是在一段更大的空白里居中、余量不贴页边，现在这份余量就是纸边距，得按口径给足。
    top_text_px = len(title_lines) * title_line + len(summary_lines) * summary_line
    bottom_text_px = max((len(lines) for lines in tip_lines), default=0) * tip_line
    top_band_px = int(top_text_px * (1 + 2 * TEXT_PAD_SHARE))
    bottom_band_px = int(bottom_text_px * (1 + 2 * TEXT_PAD_SHARE))

    paper = _paper_colour(art)
    grain_start, grain_rows = _blank_run(art, _grey_of(paper))
    grain = art.crop((0, grain_start, width, grain_start + grain_rows)) if grain_rows else None

    page = Image.new("RGB", (width, top_band_px + art.height + bottom_band_px))
    page.paste(_paper_band(art, top_band_px, paper=paper, grain=grain), (0, 0))
    page.paste(art, (0, top_band_px))
    page.paste(
        ImageOps.flip(_paper_band(ImageOps.flip(art), bottom_band_px, paper=paper, grain=grain)),
        (0, top_band_px + art.height),
    )

    pen = ImageDraw.Draw(page)
    center_x = width // 2
    y = (top_band_px - top_text_px) // 2
    y = _centered(pen, title_lines, title_font, center_x, y, title_line, _INK)
    _centered(pen, summary_lines, summary_font, center_x, y, summary_line, _SUBTLE)

    tip_top = top_band_px + art.height + (bottom_band_px - bottom_text_px) // 2
    for index, lines in enumerate(tip_lines):
        _centered(pen, lines, tip_font, column * index + column // 2, tip_top, tip_line, _INK)

    buffer = io.BytesIO()
    page.save(buffer, format="PNG", optimize=False)
    return StyleCaptioned(
        image_png=buffer.getvalue(),
        width_px=page.width,
        height_px=page.height,
        top_band_px=top_band_px,
        bottom_band_px=bottom_band_px,
    )
