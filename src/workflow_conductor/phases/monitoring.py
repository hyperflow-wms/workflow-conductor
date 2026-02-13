"""Phase 5: Execution monitoring — poll K8s job status with Rich progress."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from workflow_conductor.k8s import Kubectl
from workflow_conductor.models import PipelinePhase, PipelineState
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


async def run_monitoring_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Monitor workflow execution by polling K8s job status.

    Polls at configured intervals until all jobs complete, timeout,
    or a failure is detected.
    """
    display_phase_header(PipelinePhase.MONITORING)

    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    namespace = state.namespace
    poll_interval = settings.monitor_poll_interval
    timeout = settings.monitor_timeout

    logger.info(
        "Monitoring namespace=%s, poll=%ds, timeout=%ds",
        namespace,
        poll_interval,
        timeout,
    )

    elapsed = 0
    while elapsed < timeout:
        # Get jobs with hyperflow label
        try:
            jobs_data = await kubectl.get_json(
                "jobs",
                namespace=namespace,
                label_selector="app=hyperflow",
            )
        except Exception:
            logger.warning("Failed to query jobs, retrying...")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            continue

        items = jobs_data.get("items", [])
        total = len(items)
        completed = sum(
            1
            for j in items
            if any(
                c.get("type") == "Complete" and c.get("status") == "True"
                for c in j.get("status", {}).get("conditions", [])
            )
        )
        failed = sum(
            1
            for j in items
            if any(
                c.get("type") == "Failed" and c.get("status") == "True"
                for c in j.get("status", {}).get("conditions", [])
            )
        )

        state.total_task_count = total
        state.task_completion_count = completed

        logger.info(
            "Jobs: %d/%d completed, %d failed",
            completed,
            total,
            failed,
        )

        if total > 0 and completed + failed >= total:
            state.workflow_status = "completed" if failed == 0 else "failed"
            break

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    else:
        state.workflow_status = "timeout"
        logger.warning("Monitoring timed out after %ds", timeout)

    # Capture engine logs
    if state.engine_pod_name:
        try:
            logs = await kubectl.logs(
                state.engine_pod_name,
                namespace=namespace,
                container="hyperflow",
                tail=100,
            )
            logger.debug("Engine logs (last 100 lines):\n%s", logs)
        except Exception:
            logger.warning("Failed to capture engine logs")

    return state
