"""Phase 9: Execution monitoring — watch for workflow completion signal.

Delegates all monitoring logic to the `execution-sentinel` package via
``Sentinel.watch()``.  The Sentinel detects OOMKills, stragglers,
mass-failure escalation, and connectivity loss, streaming ``SentinelReport``
objects into a queue that is drained here and rendered via Rich.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from execution_sentinel.config import SentinelSettings
from execution_sentinel.models import MonitoringContext, TaskSpec
from execution_sentinel.sentinel import Sentinel
from execution_sentinel.ui.display import (
    display_completion_summary,
    display_report,
    display_sentinel_banner,
)

from workflow_conductor.models import PipelinePhase, PipelineState
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


def _build_monitoring_context(state: PipelineState) -> MonitoringContext:
    profile_by_type = {p.task_type: p for p in state.resource_profiles}
    task_inventory = []
    for proc in (state.workflow_json or {}).get("processes", []):
        task_type = proc.get("fun", "")
        profile = profile_by_type.get(task_type)
        task_inventory.append(
            TaskSpec(
                name=proc.get("name", ""),
                task_type=task_type,
                memory_limit=profile.memory_limit if profile else "",
                cpu_limit=profile.cpu_limit if profile else "",
                expected_duration_seconds=0.0,
            )
        )
    return MonitoringContext(
        namespace=state.namespace,
        engine_pod_name=state.engine_pod_name,
        engine_container="hyperflow",
        task_inventory=task_inventory,
    )


async def run_monitoring_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Monitor workflow execution via Execution Sentinel.

    Delegates to ``Sentinel.watch()`` which handles OOMKill detection,
    straggler detection, mass-failure escalation, and connectivity loss.
    """
    display_phase_header(PipelinePhase.MONITORING)

    if not state.engine_pod_name:
        raise ValueError("Cannot monitor: engine pod not set")

    context = _build_monitoring_context(state)
    sentinel_settings = SentinelSettings(
        kubeconfig=settings.kubernetes.kubeconfig,
        poll_interval=settings.monitor_poll_interval,
        timeout=settings.monitor_timeout,
    )
    sentinel = Sentinel(context, sentinel_settings)

    logger.info(
        "Starting Execution Sentinel: namespace=%s, engine_pod=%s, "
        "poll=%ds, timeout=%ds, expected_tasks=%d",
        context.namespace,
        context.engine_pod_name,
        sentinel_settings.poll_interval,
        sentinel_settings.timeout,
        context.total_expected_tasks,
    )

    display_sentinel_banner(context.namespace, context.total_expected_tasks)

    async def _drain() -> None:
        while True:
            report = await sentinel.reports.get()
            display_report(report)

    drain_task = asyncio.create_task(_drain())
    summary = await sentinel.watch()
    drain_task.cancel()

    # Flush any reports produced in the final iteration
    while not sentinel.reports.empty():
        display_report(sentinel.reports.get_nowait())

    display_completion_summary(summary)

    # Map WatchSummary → PipelineState
    state.task_completion_count = summary.completed_tasks
    state.total_task_count = summary.total_tasks or len(context.task_inventory)
    if summary.timed_out:
        state.workflow_status = "timeout"
    elif summary.mass_failure or (
        summary.exit_code is not None and summary.exit_code != 0
    ):
        state.workflow_status = "failed"
    else:
        state.workflow_status = "completed"

    # Capture engine logs via sentinel's kubectl client
    try:
        logs = await sentinel.kubectl.logs(
            state.engine_pod_name,
            namespace=state.namespace,
            container="hyperflow",
            tail=100,
        )
        logger.debug("Engine logs (last 100 lines):\n%s", logs)
    except Exception:
        logger.warning("Failed to capture engine logs")

    return state
