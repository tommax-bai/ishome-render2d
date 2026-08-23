# ishome-render2d

《是我的家》确定性 2D 绘图服务（`render2d-svc`）：独立部署的 Temporal worker，承接母版与确定性图层绘制（确认底图 / 功能说明图 / 风格图几何底图，含房间遮罩 / 墙体图层输出），CPU 伸缩轴。

- **出处**：V1.4 裁决（2026-08-23，绘图能力物理拆分）——中控仓《架构对齐-设计Agent×技术架构.md》§三；绘图逻辑异质 → 独立仓库 + 独立服务，无 RPC、无 schema、无状态。
- **task queue**：`render2d-activities`（namespace `genpipe`；注册表：ishome-contracts `registries/task_queues.md`）。
- **本仓 activity**（注册名唯一真源：ishome-contracts `activities/registry.md`，只增不改）：

| 注册名 | 函数名 | 职责 |
|---|---|---|
| `plan-2d-render` | `render_plan_2d` | 母版与确定性图层绘制：确认底图、功能说明图、风格图几何底图；同时输出房间遮罩/墙体图层 |

## 常用命令

```bash
uv sync                 # 安装依赖与 dev 工具
uv run ruff check .     # lint
uv run lint-imports     # import 方向契约（worker → activities → models 单向）
uv run mypy             # strict 类型检查
uv run pytest           # 测试（activity 注册名守门）
uv run render2d-worker  # 起 worker（TEMPORAL_ADDRESS，默认 localhost:7233）
```

新 clone 后执行一次：`git config core.hooksPath .githooks`（本地 pre-push 质量门）。
