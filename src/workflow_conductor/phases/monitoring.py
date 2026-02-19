"""Phase 9: Execution monitoring — watch for workflow completion signal.

HyperFlow creates K8s jobs dynamically as the workflow DAG progresses.
Monitoring job counts alone causes early exit (first batch completes before
the next is created).  The engine container itself never terminates (it
loops with ``while true; sleep 5`` to keep output files accessible).

Instead, the engine command writes a signal file
``/work_dir/.workflow-exit-code`` after ``hflow run`` exits.  We poll for
this file via ``kubectl exec``.
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

# Signal file written by the engine command after hflow exits.
_WORKFLOW_EXIT_CODE_FILE = "/work_dir/.workflow-exit-code"


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
    """Monitor workflow by polling for the engine exit-code signal file.

    The engine command writes ``/work_dir/.workflow-exit-code`` after
    ``hflow run workflow.json`` exits.  The file contains the exit code
    (0 = success, non-zero = failure).  Job counts are polled alongside
    for progress reporting.
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

        # Step 1: Check for workflow exit-code signal file
        try:
            result = await kubectl.exec_in_pod(
                state.engine_pod_name,
                ["cat", _WORKFLOW_EXIT_CODE_FILE],
                namespace=namespace,
                container="hyperflow",
            )
            exit_code = int(result.strip())
            logger.info(
                "Workflow finished: exit_code=%d",
                exit_code,
            )
            state.workflow_status = "completed" if exit_code == 0 else "failed"
            engine_done = True
        except (ValueError, TypeError):
            logger.warning("Signal file has unexpected content: %r", result)
        except Exception:
            pass  # File doesn't exist yet — workflow still running

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
