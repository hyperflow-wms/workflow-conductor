"""Phase 3: User validation gate.

Displays the workflow plan via Rich UI and prompts the user to
approve, refine, or abort. If refining, re-invokes the planning phase
with synthesized context.
"""

from __future__ import annotations

import logging

from workflow_conductor.models import PipelineState, PipelineStatus, UserResponse
from workflow_conductor.ui.display import display_workflow_plan
from workflow_conductor.ui.prompts import prompt_validation_gate

logger = logging.getLogger(__name__)


async def run_validation_phase(
    state: PipelineState,
    *,
    auto_approve: bool = False,
) -> PipelineState:
    """Display the plan and get user approval.

    Returns the state with user_approved_plan and user_modifications set.
    The caller is responsible for handling refine loops.
    """
    if state.workflow_plan is None:
        raise ValueError("No workflow plan to validate")

    display_workflow_plan(state.workflow_plan)

    response: UserResponse = prompt_validation_gate(auto_approve=auto_approve)

    if response.action == "approve":
        logger.info("User approved the workflow plan")
        state.user_approved_plan = True
        state.status = PipelineStatus.IN_PROGRESS
    elif response.action == "refine":
        logger.info("User requested refinement: %s", response.feedback)
        state.user_approved_plan = False
        state.user_modifications = response.feedback
        state.status = PipelineStatus.AWAITING_USER
    elif response.action == "abort":
        logger.info("User aborted the pipeline")
        state.user_approved_plan = False
        state.status = PipelineStatus.ABORTED

    return state
