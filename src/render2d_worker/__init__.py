"""render2d_worker：确定性 2D 绘图 activity 执行进程（render2d-svc）。

V1.4 裁决（2026-08-23）：绘图能力物理拆分——本仓承接 plan-2d-render，
独立部署 Temporal worker，专属 task queue `render2d-activities`，
无对外 RPC 端口、无数据库 schema、无状态（算完即焚，产物写 OSS + 注册
ArtifactRegistry）。伸缩轴：CPU 批量绘制。
"""
