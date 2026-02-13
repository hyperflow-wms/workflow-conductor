"""MCPApp initialization and full pipeline execution."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from mcp_agent.app import MCPApp
from mcp_agent.config import (
    MCPServerSettings,
    MCPSettings,
    Settings,
)

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import (
    PhaseResult,
    PipelinePhase,
    PipelineState,
    PipelineStatus,
)
from workflow_conductor.phases.completion import run_completion_phase
from workflow_conductor.phases.deployment import run_deployment_phase
from workflow_conductor.phases.monitoring import run_monitoring_phase
from workflow_conductor.phases.planning import run_planning_phase
from workflow_conductor.phases.routing import run_routing_phase
from workflow_conductor.phases.validation import run_validation_phase
from workflow_conductor.ui.display import (
    display_error,
    display_phase_header,
    display_pipeline_banner,
)

logger = logging.getLogger(__name__)


def create_app(settings: ConductorSettings) -> MCPApp:
    """Create and configure the MCPApp with Composer MCP server."""
    mcp_settings = Settings(
        execution_engine="asyncio",
        mcp=MCPSettings(
            servers={
                "workflow-composer": MCPServerSettings(
                    command=settings.workflow.composer_server_command,
                    args=settings.workflow.composer_server_args,
                ),
            },
        ),
    )
    return MCPApp(name="workflow-conductor", settings=mcp_settings)


async def _run_phase(
    phase: PipelinePhase,
    state: PipelineState,
    phase_fn: object,
    *args: object,
    **kwargs: object,
) -> PipelineState:
    """Execute a phase with timing and error recording."""
    state.current_phase = phase
    start = time.monotonic()
    started_at = datetime.now(UTC).isoformat()

    try:
        state = await phase_fn(state, *args, **kwargs)  # type: ignore[operator]
        elapsed = time.monotonic() - start
        state.record_phase(
            PhaseResult(
                phase=phase,
                status=PipelineStatus.COMPLETED,
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
                duration_seconds=elapsed,
            )
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        state.record_phase(
            PhaseResult(
                phase=phase,
                status=PipelineStatus.FAILED,
                error=str(exc),
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
                duration_seconds=elapsed,
            )
        )
        raise

    return state


async def run_pipeline(
    prompt: str,
    settings: ConductorSettings,
    *,
    dry_run: bool = False,
    auto_approve: bool = False,
) -> PipelineState:
    """Execute the full 6-phase pipeline.

    Phases: ROUTING -> PLANNING -> VALIDATION -> DEPLOYMENT
            -> MONITORING -> COMPLETION

    If dry_run is True, stops after VALIDATION.
    """
    state = PipelineState(
        user_prompt=prompt,
        execution_id=datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
        created_at=datetime.now(UTC).isoformat(),
        status=PipelineStatus.IN_PROGRESS,
    )
    state.add_conversation("user", prompt)

    display_pipeline_banner(prompt, dry_run=dry_run)

    try:
        # Phase 1: Routing
        state = await _run_phase(PipelinePhase.ROUTING, state, run_routing_phase)

        # Phase 2: Planning
        display_phase_header(PipelinePhase.PLANNING)
        state = await _run_phase(
            PipelinePhase.PLANNING, state, run_planning_phase, settings
        )

        # Phase 3: Validation
        display_phase_header(PipelinePhase.VALIDATION)
        state = await _run_phase(
            PipelinePhase.VALIDATION,
            state,
            run_validation_phase,
            auto_approve=auto_approve or settings.auto_approve,
        )

        if state.status == PipelineStatus.ABORTED:
            logger.info("Pipeline aborted by user")
            return state

        if dry_run:
            logger.info("Dry run complete — skipping deployment")
            state.status = PipelineStatus.COMPLETED
            return state

        if not state.user_approved_plan:
            logger.info("Plan not approved, stopping pipeline")
            return state

        # Phase 4: Deployment
        state = await _run_phase(
            PipelinePhase.DEPLOYMENT,
            state,
            run_deployment_phase,
            settings,
        )

        # Phase 5: Monitoring
        state = await _run_phase(
            PipelinePhase.MONITORING,
            state,
            run_monitoring_phase,
            settings,
        )

        # Phase 6: Completion
        state = await _run_phase(
            PipelinePhase.COMPLETION,
            state,
            run_completion_phase,
            settings,
        )

    except Exception as exc:
        state.status = PipelineStatus.FAILED
        display_error(str(exc), phase=state.current_phase.value)
        logger.exception("Pipeline failed at phase %s", state.current_phase)

    return state
