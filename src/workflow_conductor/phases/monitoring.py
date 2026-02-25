"""Phase 9: Execution monitoring — watch for workflow completion signal.

Delegates all monitoring logic to the `execution-sentinel` package via
``Sentinel.watch()``.  The Sentinel detects OOMKills, stragglers,
mass-failure escalation, and connectivity loss, streaming ``SentinelReport``
objects into a queue that is drained here and rendered via Rich.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from execution_sentinel.config import SentinelSettings
from execution_sentinel.models import MonitoringContext, SentinelReport, TaskSpec
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

# Known 1000 Genomes task ordering and default durations
_1000G_TASK_ORDER: dict[str, int] = {
    "individuals": 0,
    "individuals_merge": 1,
    "sifting": 2,
    "mutation_overlap": 3,
    "frequency": 4,
}
_1000G_TASK_DEPS: dict[str, list[str]] = {
    "individuals": [],
    "individuals_merge": ["individuals"],
    "sifting": ["individuals_merge"],
    "mutation_overlap": ["sifting"],
    "frequency": ["sifting"],
}
_1000G_DEFAULT_DURATIONS: dict[str, float] = {
    "individuals": 30.0,
    "individuals_merge": 10.0,
    "sifting": 45.0,
    "mutation_overlap": 20.0,
    "frequency": 15.0,
}


# TODO: re-enable when hf-engine K8s service name/port confirmed from Helm charts
# def _build_hyperflow_endpoint(state: PipelineState) -> str:
#     if state.namespace:
#         return f"http://hf-engine.{state.namespace}.svc.cluster.local:8080"
#     return ""


def _build_monitoring_context(state: PipelineState) -> MonitoringContext:
    profile_by_type = {p.task_type: p for p in state.resource_profiles}
    task_inventory = []
    for proc in (state.workflow_json or {}).get("processes", []):
        task_type = proc.get("fun", "")
        profile = profile_by_type.get(task_type)
        dag_order = _1000G_TASK_ORDER.get(task_type, 99)
        expected_duration = (
            profile.expected_duration_seconds
            if (profile and profile.expected_duration_seconds > 0)
            else _1000G_DEFAULT_DURATIONS.get(task_type, 0.0)
        )
        task_inventory.append(
            TaskSpec(
                name=proc.get("name", ""),
                task_type=task_type,
                memory_limit=profile.memory_limit if profile else "",
                cpu_limit=profile.cpu_limit if profile else "",
                dag_order=dag_order,
                expected_duration_seconds=expected_duration,
            )
        )
    return MonitoringContext(
        namespace=state.namespace,
        engine_pod_name=state.engine_pod_name,
        engine_container="hyperflow",
        task_inventory=task_inventory,
        dag_structure=_1000G_TASK_DEPS,
        # hyperflow_api_endpoint=_build_hyperflow_endpoint(state),  # TODO: re-enable when confirmed
    )


async def _translate_to_nl(report: SentinelReport, settings: "ConductorSettings") -> str:
    """Translate a Sentinel report into a science-friendly sentence for the user."""
    try:
        import anthropic  # type: ignore[import-untyped]

        client = anthropic.AsyncAnthropic()
        prompt = (
            f"You are an AI assistant helping a genomics researcher. "
            f"Translate this Kubernetes workflow monitoring event into one clear, "
            f"science-friendly sentence (no Kubernetes jargon).\n"
            f"Event: kind={report.kind}, message={report.message!r}, "
            f"progress={report.completed_tasks}/{report.total_tasks}."
        )
        response = await client.messages.create(
            model=settings.llm.anthropic_model,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()  # type: ignore[index]
    except Exception:  # noqa: BLE001
        return report.message


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
            report.nl_message = await _translate_to_nl(report, settings)
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
