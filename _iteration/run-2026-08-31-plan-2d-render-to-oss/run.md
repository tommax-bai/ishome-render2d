# 真跑 · 母版这一批进私有桶 · 2026-08-31 晚

> 目的：`plan-2d-render` 从**一个 raise NotImplementedError 的存根**变成**真的能画、并把产物
> 写进私有桶**。此前会画的代码只有 CLI 调得到。
> 纪律：本文所有数与状态**逐字取自真跑**；失败路径一并留档。

## 一、跑法与进程组

**两个进程**（本轮只验本仓这一步，没有起编排上游）：

```
ishome-dev-temporal :7233（常驻，namespace genpipe）
render2d-worker                (本仓，队列 render2d-activities)   ← 本轮新增
```

```bash
# 1) 几何（在 ishome-aipipe，零模型调用；--survey 复用存档即整轮零调用可复现）
uv run floorplan-geometry \
  --image services/genpipe-worker/samples/floorplan-brochure-92sqm-3b2l1b.png \
  --survey _iteration/run-2026-08-30-floorplan-geometry/geometry-1.json -o out/geo

# 2) 起 worker
set -a; source ~/.ishome/oss-local.env; set +a
uv run render2d-worker

# 3) 派发（临时 workflow 只编排、不注册 activity，跑在自己的队列上）
uv run python dispatch.py dispatch.json plan-2d-render-realrun-2026-08-31-1
```

## 二、结果：一次通过

样例＝楼书 92㎡ 三室（`content_sha256` `e2a41bfe…a514`，与几何存档里那张图逐字同源）。
workflow `plan-2d-render-realrun-2026-08-31-1`，Temporal 状态 **Completed**，`verdict = ok`。

| 项 | 逐字 |
|---|---|
| 母版尺寸 | 1320 × 1600 px |
| 外圈闭合率 | **0.9373**（门槛 0.90，母版自己的自证数） |
| 房间 | 9 间 |
| 批注 | 5 条 |
| 落桶 | 桶 `ishome`，5 件 |

五条对象键（**都在源户型图那一条前缀底下**）：

| 产物 | 键 | 字节 | Content-Type |
|---|---|---|---|
| 母版 | `uploads/e2a41bfe…a514/plan-master.png` | 6 101 | `image/png` |
| 墙体图层 | `uploads/e2a41bfe…a514/plan-walls.png` | 5 920 | `image/png` |
| 房间遮罩索引图 | `uploads/e2a41bfe…a514/plan-rooms-mask.png` | 5 099 | `image/png` |
| 房间锚点清单 | `uploads/e2a41bfe…a514/plan-rooms.json` | 905 | `application/json; charset=utf-8` |
| 功能说明图 | `uploads/e2a41bfe…a514/plan-brief.png` | 118 140 | `image/png` |

**回读校验**：五件全部取回来，与本地纯库画的**逐字节相等**。这一条同时验了两件事——
写进去的字节没变，且 **CLI 那条路与 activity 那条路画的是同一张图**（同一份纯库代码）。

**幂等**：同一份入参派发了两次，键与字节完全相同，桶里始终只有这五个对象。

## 三、两条安全口径，都是实测不是声称

1. **桶确实私有**：无签名直取 `plan-master.png` → `HTTP 403`，`<Code>AccessDenied</Code>`。
   这是"用户私有产物不进公开内容库"（获客线红线二）在存储层的实测证据。
2. **前缀底下只有该有的五个对象**：失败那一跑（下节）一件都没写。

## 四、失败路径：两条都实测过

**画不出来就是这一步失败，不许回报成功。**

| 场景 | 实测 |
|---|---|
| 几何缺外轮廓（把 `outline` 清空重派一次） | `verdict = failed`，`plan-master-failed`：*"外圈没闭合：户型轮廓只有 **64%** 被墙盖住（门槛 90%）"*——与这条门禁上线当天拦下自己那次**同一个数**；桶里一件没写 |
| worker 缺私有桶凭证 | **起不来**，一句人话并点名缺的是哪几个：*"私有对象存储没配全，缺：ISHOME_OSS_ENDPOINT、…——凭证放 ~/.ishome/oss-local.env（本机）或 /opt/ishome/env/oss.env（服务器），不入库"*。不带着半套配置上线等第一张图去踩 |

## 五、留档与两处如实说明

- `dispatch.json`：本轮派发请求体逐字（几何是产出侧原样那一份，camelCase）。

两处**这次没做、说清楚**：

1. **源图本身不在桶里**。`uploads/e2a41bfe…a514/original.png` 这条键是那张真样例的真实派生形态，
   但写它的是 channel-svc（收图即落桶），本轮没有替它写——activity 全程不读源图，
   只拿这条键派生产物键。
2. **五条批注是手写的**，不是模型产的。真跑要验的是"说明图画不画得出来、落不落得了桶"；
   批注内容归 aipipe 那一步，它有自己的真跑（《三张图出得来》§六：过检 5 条打回 0 条）。

## 六、还没做的

**派发方还不存在**：眼下是拿一个临时 workflow 手动派一次。接进 genpipe 编排属"上传入口接线"
那一批（业务侧铸解析任务并派发那一段），触发条件即那条线开工时。
**叠字（`style_caption`）没进 activity**：它吃的是 imagegen 产的风格图不是几何，
contracts 注册表里也没有它的名字——要进编排得先走 contracts PR 加注册名。
