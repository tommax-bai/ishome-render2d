"""worker 进程装配：连接 Temporal（namespace `genpipe`），监听 `render2d-activities`。

**组合根在此**：私有桶连接在这里装好并当场校验——装不上就起不来，绝不带着半套配置上线
等第一张母版去踩（"缺配置要在起不来的时候就知道"）。

genpipe workflow 按 activity 归属把任务派到本仓专属 task queue；重试/心跳/取消/
背压沿用 Temporal activity 原生语义，不引入服务间 HTTP 调用（对齐文档 §3.1）。
"""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from render2d_worker.activities import PlanRenderer, activity_registry
from render2d_worker.plan_store import OssPlanStore, OssSettings, PlanStoreError

GENPIPE_NAMESPACE = "genpipe"
RENDER2D_TASK_QUEUE = "render2d-activities"
"""contracts `registries/task_queues.md` 逐字一致（只增不改）。"""


async def run_worker(temporal_address: str) -> None:
    try:
        store = OssPlanStore(OssSettings.from_env())
    except PlanStoreError as e:
        # 缺配置是**运维要看的一句话**，不是给开发看的调用栈：起不来的原因要一眼读得懂。
        raise SystemExit("；".join(e.details)) from None
    renderer = PlanRenderer(store)
    client = await Client.connect(temporal_address, namespace=GENPIPE_NAMESPACE)
    worker = Worker(
        client,
        task_queue=RENDER2D_TASK_QUEUE,
        activities=list(activity_registry(renderer).values()),
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")))


if __name__ == "__main__":
    main()
