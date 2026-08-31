# ishome-render2d

《是我的家》确定性 2D 绘图服务（`render2d-svc`）：独立部署的 Temporal worker，承接母版与确定性图层绘制（确认底图 / 功能说明图 / 风格图几何底图，含房间遮罩 / 墙体图层输出），CPU 伸缩轴。

- **出处**：V1.4 裁决（2026-08-23，绘图能力物理拆分）——中控仓《架构对齐-设计Agent×技术架构.md》§三；绘图逻辑异质 → 独立仓库 + 独立服务，无 RPC、无 schema、无状态。
- **task queue**：`render2d-activities`（namespace `genpipe`；注册表：ishome-contracts `registries/task_queues.md`）。
- **本仓 activity**（注册名唯一真源：ishome-contracts `activities/registry.md`，只增不改）：

| 注册名 | 函数名 | 职责 |
|---|---|---|
| `plan-2d-render` | `render_plan_2d` | 母版与确定性图层绘制：确认底图、功能说明图、风格图几何底图；同时输出房间遮罩/墙体图层 |

**形态**：2026-08-31 之前是纯库 + CLI（"接进 activity 的时点写死＝派发链路接通时"），
现已**成服务**——worker 监听 `render2d-activities`，产物写进私有对象存储。

**CLI 不废**：它是本地迭代的入口——改样式、看一张图长什么样走它，不必起 Temporal，也不碰对象存储。
两条路共用同一份纯库代码（`plan_master` / `plan_brief`），**不许出现两套画法**；分界由
import-linter 锁死（`cli` 既看不见 `activities` 也看不见 `plan_store`）：从它能看见起，
"本地画一张图不需要凭证"就只是一句承诺而不是结构。

## 用法

```bash
# 本地画一张（零凭证、零 Temporal）
uv run render2d --geometry ~/codes/ishome-aipipe/out/geo/*-geometry.json -o out/master
```

### 出图服务（`render2d-worker`）

```bash
set -a; source ~/.ishome/oss-local.env; set +a   # 私有桶凭证，不入库
uv run render2d-worker                           # 监听 render2d-activities
```

**入参是不透明字典**（派发方不 import 本仓签名，两边只靠 contracts 注册名接头）：

| 字段 | 形态 |
|---|---|
| `floorplan_object_key` | 源户型图在私有桶里的键 `uploads/{content_sha256}/original.{ext}`（channel-svc 收图时落的）。产物键由它同前缀派生 |
| `geometry` | 几何提取的产物，**内联传**（不大的一份 JSON，且几何今天还没有落桶的键；改走键的时点写死＝几何产物进 contracts `registries/object_keys.md` 那一次） |
| `notes` | 批注，可缺。给了就多出一张功能说明图；确认底图那一步本来就还没有批注可画 |

**出参一律是对象键**（一张 1600px 的母版塞进编排的返回值是拿 Temporal 当文件传输通道用）：

| 产物 | 对象键 | 谁用得着 |
|---|---|---|
| 母版 | `uploads/{content_sha256}/plan-master.png` | imagegen 图生图的几何底图；也是给业主看的确认底图 |
| 墙体图层 | `uploads/{content_sha256}/plan-walls.png` | 几何跟随度与配准 |
| 房间遮罩索引图 | `uploads/{content_sha256}/plan-rooms-mask.png` | 逐房间比对重合度 |
| 房间锚点清单 | `uploads/{content_sha256}/plan-rooms.json` | 风格图的房间表（不填它，厨房会跑到次卧的位置） |
| 功能说明图 | `uploads/{content_sha256}/plan-brief.png` | channel-svc 发给业主的成品 |

- **键确定性派生、不铸流水号、不含用户身份与渠道方言**——三条理由见 contracts
  `registries/object_keys.md` 开头。源图键是那张表的逐字副本；**产物键是本轮的默认**，
  入表的时点写死＝中控仓那侧统一改那张表那一次。因此"这张户型图出没出图"**问存储即知，
  不另立台账**；同一张图重跑覆盖同一批对象，天然幂等。
- **只写不签**。签名是"给谁看、看多久"的事，属业务侧——生成侧不知用户是谁。
- **画不出来、写不进去，都不是 ok**：外圈闭合率不达标、批注挂不上房间、没有中文字体、
  桶写不进——一律 `verdict: failed` 并逐条回报，绝不回一个指向空气的键。

## 红线（违反即返工）

1. **确定性、零模型调用**：同一份几何画两次逐字节相同；**几何不由 LLM 决定**。
2. **母版上不写字**：房间名与批注归说明图那一步，母版因此不依赖任何字体。
3. **外圈闭合率低于 0.90 整张不出**：母版是几何唯一源，外墙漏风的底子下游没有一步能发现。
4. **字体找不到整张不出，不画豆腐块**：本机有服务器没有，是"静默降级"最典型的形态。

## 质量门

本地 pre-push（新 clone 后执行一次 `git config core.hooksPath .githooks`）：
ruff / ruff format / import-linter / mypy strict / pytest。

```bash
uv sync                 # 安装依赖与 dev 工具
uv run ruff check .     # lint
uv run ruff format .    # 排版
uv run lint-imports     # import 方向契约（三条：分层单向、plan_store 不感知上层、cli 不碰存储）
uv run mypy             # strict 类型检查
uv run pytest           # 测试
```
