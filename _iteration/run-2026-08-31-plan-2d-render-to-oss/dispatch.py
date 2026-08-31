"""真跑派发：起一个临时 workflow worker，把 `plan-2d-render` 派到 `render2d-activities`。

Temporal 的 activity 只能由 workflow 调起，所以这里带一个最小 workflow；它跑在自己的临时
队列上，只编排、不注册任何 activity——真正干活的是另一个进程里的 render2d-worker。
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

RENDER2D_TASK_QUEUE = "render2d-activities"
REALRUN_TASK_QUEUE = "render2d-realrun-workflows"


@workflow.defn(name="plan-2d-render-realrun")
class PlanRenderRealRunWorkflow:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await workflow.execute_activity(
            "plan-2d-render",
            request,
            task_queue=RENDER2D_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=2),
            schedule_to_close_timeout=timedelta(minutes=3),
            # 一次就够：失败要当场看见，不要被重试盖住
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return result


async def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    workflow_id = sys.argv[2]
    client = await Client.connect("localhost:7233", namespace="genpipe")
    async with Worker(client, task_queue=REALRUN_TASK_QUEUE, workflows=[PlanRenderRealRunWorkflow]):
        result = await client.execute_workflow(
            PlanRenderRealRunWorkflow.run,
            request,
            id=workflow_id,
            task_queue=REALRUN_TASK_QUEUE,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
