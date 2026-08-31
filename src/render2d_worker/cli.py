"""CLI：`render2d --geometry geometry.json -o out/`。

工具形态先行（同渲染层 2026-08-29 那条裁决的形态）：母版先以命令行工具存在，
**接进 activity 的时点写死＝上传入口把解析派发接通时**。先有能画出来的东西，
再谈它在编排里怎么被调——反过来是接一遍再改一遍。

产出四件：母版、墙体图层、房间遮罩索引图、房间锚点清单。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from render2d_worker.models import FloorplanGeometry
from render2d_worker.plan_master import (
    DEFAULT_LONG_SIDE_PX,
    DEFAULT_MARGIN_PX,
    PlanMasterError,
    render_plan_master,
    room_anchors_json,
)

MASTER_PNG = "plan-master.png"
WALLS_PNG = "walls.png"
ROOMS_PNG = "rooms.png"
ROOMS_JSON = "rooms.json"


def _load_geometry(path: Path) -> FloorplanGeometry:
    with path.open(encoding="utf-8") as f:
        payload: Any = json.load(f)
    # 几何提取的 CLI 把产物裹在 {"geometry": {...}} 里，直接喂那一份也认
    if isinstance(payload, dict) and "geometry" in payload:
        payload = payload["geometry"]
    return FloorplanGeometry.model_validate(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="render2d",
        description="母版绘制：几何 → 母版 + 墙体图层 + 房间遮罩（确定性、零模型调用）",
    )
    parser.add_argument("--geometry", required=True, type=Path, help="几何提取产物 JSON")
    parser.add_argument("-o", "--out", type=Path, default=Path("out"), help="产物目录")
    parser.add_argument("--long-side-px", type=int, default=DEFAULT_LONG_SIDE_PX)
    parser.add_argument("--margin-px", type=int, default=DEFAULT_MARGIN_PX)
    args = parser.parse_args(argv)

    try:
        geometry = _load_geometry(args.geometry)
    except (OSError, ValueError, ValidationError) as e:
        print(f"读几何失败：{e}", file=sys.stderr)
        return 2

    try:
        master = render_plan_master(
            geometry, long_side_px=args.long_side_px, margin_px=args.margin_px
        )
    except PlanMasterError as e:
        print("母版画不出来（fail loud，不给一张差不多的图）：", file=sys.stderr)
        for line in e.details:
            print(f"  - {line}", file=sys.stderr)
        return 3

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / MASTER_PNG).write_bytes(master.master_png)
    (args.out / WALLS_PNG).write_bytes(master.walls_png)
    (args.out / ROOMS_PNG).write_bytes(master.rooms_png)
    (args.out / ROOMS_JSON).write_bytes(room_anchors_json(master.rooms))

    print(f"母版已出：{args.out}（{master.width_px}×{master.height_px}px）")
    print(
        f"墙 {len(geometry.walls)} 段、洞 {len(geometry.openings)} 个、房间 {len(master.rooms)} 间"
    )
    for room in master.rooms:
        print(f"  {room.mask_index}. {room.name} 锚点 {room.anchor_x_px},{room.anchor_y_px}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
