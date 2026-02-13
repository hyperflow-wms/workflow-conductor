"""Phase 6: Completion — teardown and summary reporting."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from workflow_conductor.k8s import Helm, Kubectl
from workflow_conductor.models import (
    ExecutionSummary,
    PipelinePhase,
    PipelineState,
    PipelineStatus,
)
from workflow_conductor.ui.display import (
    display_completion_summary,
    display_phase_header,
)

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


async def run_completion_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Perform teardown and produce execution summary."""
    display_phase_header(PipelinePhase.COMPLETION)

    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    helm = Helm(kubeconfig=settings.kubernetes.kubeconfig)
    namespace = state.namespace

    # Build execution summary
    total_runtime = sum(state.phase_timings.values())
    state.execution_summary = ExecutionSummary(
        total_tasks=state.total_task_count,
        completed_tasks=state.task_completion_count,
        failed_tasks=max(0, state.total_task_count - state.task_completion_count),
        total_runtime_seconds=total_runtime,
    )

    # Teardown if configured
    if settings.auto_teardown and namespace:
        logger.info("Tearing down namespace: %s", namespace)
        try:
            for release in ["hf-run", "hf-data", "hf-ops"]:
                if await helm.release_exists(release, namespace=namespace):
                    await helm.uninstall(release, namespace=namespace)
            await kubectl.delete_namespace(namespace)
            state.teardown_completed = True
        except Exception:
            logger.warning("Teardown encountered errors", exc_info=True)

    # Set final status
    if state.workflow_status == "completed":
        state.status = PipelineStatus.COMPLETED
    elif state.workflow_status == "failed":
        state.status = PipelineStatus.FAILED
    else:
        state.status = PipelineStatus.FAILED

    display_completion_summary(state)

    return state
