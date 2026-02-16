"""MCPApp initialization and full pipeline execution."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from mcp_agent.app import MCPApp
from mcp_agent.config import (
    AnthropicSettings,
    GoogleSettings,
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
from workflow_conductor.phases.approval import run_approval_phase
from workflow_conductor.phases.completion import run_completion_phase
from workflow_conductor.phases.deployment import run_deployment_phase
from workflow_conductor.phases.generation import run_generation_phase
from workflow_conductor.phases.monitoring import run_monitoring_phase
from workflow_conductor.phases.planning import run_planning_phase
from workflow_conductor.phases.provisioning import run_provisioning_phase
from workflow_conductor.phases.routing import run_routing_phase
from workflow_conductor.phases.validation import run_validation_phase
from workflow_conductor.ui.display import (
    demo_pause,
    display_error,
    display_phase_explanation,
    display_pipeline_banner,
    display_workflow_json_summary,
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
        anthropic=AnthropicSettings(
            default_model=settings.llm.anthropic_model,
        ),
        google=GoogleSettings(
            default_model=settings.llm.google_model,
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
    demo: bool = False,
) -> PipelineState:
    """Execute the full 9-phase pipeline.

    Phases: ROUTING -> PLANNING -> VALIDATION (Gate 1) -> PROVISIONING
            -> GENERATION -> APPROVAL (Gate 2) -> DEPLOYMENT -> MONITORING
            -> COMPLETION

    If dry_run is True, stops after VALIDATION (Gate 1).
    If demo is True, shows phase explanations and pauses between phases.
    """
    # Store demo flag in settings so completion phase can check it
    settings.demo = demo

    state = PipelineState(
        user_prompt=prompt,
        execution_id=datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
        created_at=datetime.now(UTC).isoformat(),
        status=PipelineStatus.IN_PROGRESS,
    )
    state.add_conversation("user", prompt)

    display_pipeline_banner(prompt, dry_run=dry_run, demo=demo)

    try:
        # Phase 1: Routing
        if demo:
            display_phase_explanation(PipelinePhase.ROUTING)
        state = await _run_phase(PipelinePhase.ROUTING, state, run_routing_phase)
        if demo:
            demo_pause()

        # Phase 2: Planning
        if demo:
            display_phase_explanation(PipelinePhase.PLANNING)
        state = await _run_phase(
            PipelinePhase.PLANNING, state, run_planning_phase, settings
        )
        if demo:
            demo_pause()

        # Phase 3: Validation (Gate 1)
        if demo:
            display_phase_explanation(PipelinePhase.VALIDATION)
        state = await _run_phase(
            PipelinePhase.VALIDATION,
            state,
            run_validation_phase,
            auto_approve=auto_approve or settings.auto_approve,
        )
        if demo:
            demo_pause()

        if state.status == PipelineStatus.ABORTED:
            logger.info("Pipeline aborted by user at Gate 1")
            return state

        if dry_run:
            logger.info("Dry run complete — skipping deployment")
            state.status = PipelineStatus.COMPLETED
            return state

        if not state.user_approved_plan:
            logger.info("Plan not approved, stopping pipeline")
            return state

        # Phase 4: Provisioning
        if demo:
            display_phase_explanation(PipelinePhase.PROVISIONING)
        state = await _run_phase(
            PipelinePhase.PROVISIONING,
            state,
            run_provisioning_phase,
            settings,
        )
        if demo:
            demo_pause()

        # Phase 5: Generation
        if demo:
            display_phase_explanation(PipelinePhase.GENERATION)
        state = await _run_phase(
            PipelinePhase.GENERATION,
            state,
            run_generation_phase,
            settings,
        )
        if demo and state.workflow_json:
            display_workflow_json_summary(state.workflow_json)
        if demo:
            demo_pause()

        # Phase 6: Approval (Gate 2)
        if demo:
            display_phase_explanation(PipelinePhase.APPROVAL)
        state = await _run_phase(
            PipelinePhase.APPROVAL,
            state,
            run_approval_phase,
            auto_approve=auto_approve or settings.auto_approve,
            max_processes=settings.max_workflow_processes,
        )
        if demo:
            demo_pause()

        if state.status == PipelineStatus.ABORTED:
            logger.info("Pipeline aborted by user at Gate 2")
            return state

        if not state.user_approved_execution:
            logger.info("Execution not approved, stopping pipeline")
            return state

        # Phase 7: Deployment
        if demo:
            display_phase_explanation(PipelinePhase.DEPLOYMENT)
        state = await _run_phase(
            PipelinePhase.DEPLOYMENT,
            state,
            run_deployment_phase,
            settings,
        )
        if demo:
            demo_pause()

        # Phase 8: Monitoring
        if demo:
            display_phase_explanation(PipelinePhase.MONITORING)
        state = await _run_phase(
            PipelinePhase.MONITORING,
            state,
            run_monitoring_phase,
            settings,
        )
        if demo:
            demo_pause(
                f"Inspect cluster: kubectl get pods -n {state.namespace} "
                "| Press Enter to tear down and finish..."
            )

        # Phase 9: Completion
        if demo:
            display_phase_explanation(PipelinePhase.COMPLETION)
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
