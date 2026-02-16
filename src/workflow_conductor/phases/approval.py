"""Approval phase (Gate 2): execution approval with real metrics.

Shows the user actual task counts, resource info, and infrastructure
details before proceeding with deployment.
"""

from __future__ import annotations

import logging

from workflow_conductor.models import PipelinePhase, PipelineState, PipelineStatus
from workflow_conductor.ui.display import (
    display_execution_preview,
    display_phase_header,
)
from workflow_conductor.ui.prompts import prompt_execution_gate

logger = logging.getLogger(__name__)


async def run_approval_phase(
    state: PipelineState,
    *,
    auto_approve: bool = False,
) -> PipelineState:
    """Present execution preview and prompt for deployment approval.

    Requires workflow_json to be populated (from generation phase).
    Sets state.user_approved_execution and state.total_task_count.
    """
    display_phase_header(PipelinePhase.APPROVAL)

    if state.workflow_json is None:
        raise ValueError("Cannot approve execution: workflow_json not generated")

    # Extract task count from workflow
    processes = state.workflow_json.get("processes", [])
    state.total_task_count = len(processes)

    # Show preview
    display_execution_preview(state)

    # Prompt user
    response = prompt_execution_gate(auto_approve=auto_approve)

    if response.action == "approve":
        state.user_approved_execution = True
        logger.info("Execution approved (%d tasks)", state.total_task_count)
    else:
        state.user_approved_execution = False
        state.status = PipelineStatus.ABORTED
        logger.info("Execution aborted by user at Gate 2")

    return state
