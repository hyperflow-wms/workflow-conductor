"""Phase 9: Execution monitoring — watch engine container for completion.

HyperFlow creates K8s jobs dynamically as the workflow DAG progresses.
Monitoring job counts alone causes early exit (first batch completes before
the next is created).  Instead, we watch the engine pod's 'hyperflow'
container: when the engine process exits, the workflow is done.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from workflow_conductor.k8s import Kubectl
from workflow_conductor.models import PipelinePhase, PipelineState
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


def _count_jobs(items: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Return (total, completed, failed) counts from a jobs list."""
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
    return total, completed, failed


async def run_monitoring_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Monitor workflow by watching the HyperFlow engine container.

    The engine runs ``hflow run workflow.json``.  When all DAG tasks
    finish, the process exits (code 0 = success, non-zero = failure).
    Job counts are polled alongside for progress reporting.
    """
    display_phase_header(PipelinePhase.MONITORING)

    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    namespace = state.namespace
    poll_interval = settings.monitor_poll_interval
    timeout = settings.monitor_timeout

    if not state.engine_pod_name:
        raise ValueError("Cannot monitor: engine pod not set")

    logger.info(
        "Monitoring engine pod=%s, namespace=%s, poll=%ds, timeout=%ds",
        state.engine_pod_name,
        namespace,
        poll_interval,
        timeout,
    )

    elapsed = 0
    while elapsed < timeout:
        engine_done = False

        # Step 1: Check engine container status (definitive signal)
        try:
            pod_data = await kubectl.get_json(
                f"pod/{state.engine_pod_name}",
                namespace=namespace,
            )
            for cs in pod_data.get("status", {}).get("containerStatuses", []):
                if cs.get("name") != "hyperflow":
                    continue
                terminated = cs.get("state", {}).get("terminated")
                if terminated is not None:
                    exit_code = terminated.get("exitCode", -1)
                    reason = terminated.get("reason", "")
                    logger.info(
                        "Engine terminated: exit_code=%d, reason=%s",
                        exit_code,
                        reason,
                    )
                    state.workflow_status = "completed" if exit_code == 0 else "failed"
                    engine_done = True
                    break
        except Exception:
            logger.warning("Failed to query engine pod status, retrying...")

        # Step 2: Poll job counts for progress reporting
        try:
            jobs_data = await kubectl.get_json(
                "jobs",
                namespace=namespace,
                label_selector="app=hyperflow",
            )
            total, completed, failed = _count_jobs(jobs_data.get("items", []))
            state.total_task_count = total
            state.task_completion_count = completed
            logger.info(
                "Jobs: %d/%d completed, %d failed",
                completed,
                total,
                failed,
            )
        except Exception:
            logger.warning("Failed to query job counts")

        if engine_done:
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
