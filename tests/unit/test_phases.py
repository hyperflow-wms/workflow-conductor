"""Unit tests for pipeline phases — mocked MCP tool calls."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import (
    PipelineState,
    PipelineStatus,
    WorkflowPlan,
)
from workflow_conductor.phases.routing import SUPPORTED_DOMAIN, run_routing_phase
from workflow_conductor.phases.validation import run_validation_phase


class TestRoutingPhase:
    @pytest.mark.asyncio
    async def test_routes_to_1000genome(self) -> None:
        state = PipelineState(user_prompt="Analyze EUR, chr 22")
        result = await run_routing_phase(state)
        assert result.intent_classification == SUPPORTED_DOMAIN

    @pytest.mark.asyncio
    async def test_routing_preserves_prompt(self) -> None:
        state = PipelineState(user_prompt="My research query")
        result = await run_routing_phase(state)
        assert result.user_prompt == "My research query"


class TestPlanningPhase:
    @pytest.mark.asyncio
    async def test_planning_creates_workflow_plan(self) -> None:
        """Test that planning phase calls Agent and produces a WorkflowPlan."""
        from workflow_conductor.phases.planning import run_planning_phase

        mock_llm = AsyncMock()
        plan_json = (
            '{"description": "EUR chr22 analysis",'
            ' "chromosomes": ["22"],'
            ' "populations": ["EUR"],'
            ' "parallelism": 4}'
        )
        mock_llm.generate_str.return_value = plan_json
        mock_llm.conversation_history = [{"role": "assistant", "content": "planned"}]

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        state = PipelineState(user_prompt="Analyze EUR population, chromosome 22")
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.planning.Agent", return_value=mock_agent):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        assert result.workflow_plan.chromosomes == ["22"]
        assert result.workflow_plan.populations == ["EUR"]
        assert result.workflow_plan.parallelism == 4
        assert len(result.planner_history) == 1

    @pytest.mark.asyncio
    async def test_planning_handles_non_json_response(self) -> None:
        """Test graceful handling when LLM returns plain text."""
        from workflow_conductor.phases.planning import run_planning_phase

        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = "I planned a workflow for EUR chr22."
        mock_llm.conversation_history = []

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        state = PipelineState(user_prompt="Analyze EUR")
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.planning.Agent", return_value=mock_agent):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        assert "planned a workflow" in result.workflow_plan.description

    @pytest.mark.asyncio
    async def test_planning_unsupported_provider_raises(self) -> None:
        from workflow_conductor.phases.planning import run_planning_phase

        state = PipelineState(user_prompt="test")
        settings = ConductorSettings(llm={"default_provider": "openai"})

        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            await run_planning_phase(state, settings)

    @pytest.mark.asyncio
    async def test_planning_uses_google_provider(self) -> None:
        from workflow_conductor.phases.planning import run_planning_phase

        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = '{"description": "test"}'
        mock_llm.conversation_history = []

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        state = PipelineState(user_prompt="test")
        settings = ConductorSettings(llm={"default_provider": "google"})

        with patch("workflow_conductor.phases.planning.Agent", return_value=mock_agent):
            result = await run_planning_phase(state, settings)

        # Verify Google LLM class was passed to attach_llm
        from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM

        mock_agent.attach_llm.assert_called_once_with(GoogleAugmentedLLM)
        assert result.workflow_plan is not None


class TestValidationPhase:
    @pytest.mark.asyncio
    async def test_auto_approve(self) -> None:
        state = PipelineState(
            workflow_plan=WorkflowPlan(description="test plan"),
        )
        result = await run_validation_phase(state, auto_approve=True)
        assert result.user_approved_plan is True
        assert result.status == PipelineStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_user_approves(self) -> None:
        from workflow_conductor.models import UserResponse

        state = PipelineState(
            workflow_plan=WorkflowPlan(description="test plan"),
        )
        with patch(
            "workflow_conductor.phases.validation.prompt_validation_gate",
            return_value=UserResponse(action="approve"),
        ):
            result = await run_validation_phase(state)
        assert result.user_approved_plan is True

    @pytest.mark.asyncio
    async def test_user_refines(self) -> None:
        from workflow_conductor.models import UserResponse

        state = PipelineState(
            workflow_plan=WorkflowPlan(description="test plan"),
        )
        with patch(
            "workflow_conductor.phases.validation.prompt_validation_gate",
            return_value=UserResponse(action="refine", feedback="add chr 1"),
        ):
            result = await run_validation_phase(state)
        assert result.user_approved_plan is False
        assert result.user_modifications == "add chr 1"
        assert result.status == PipelineStatus.AWAITING_USER

    @pytest.mark.asyncio
    async def test_user_aborts(self) -> None:
        from workflow_conductor.models import UserResponse

        state = PipelineState(
            workflow_plan=WorkflowPlan(description="test plan"),
        )
        with patch(
            "workflow_conductor.phases.validation.prompt_validation_gate",
            return_value=UserResponse(action="abort"),
        ):
            result = await run_validation_phase(state)
        assert result.user_approved_plan is False
        assert result.status == PipelineStatus.ABORTED

    @pytest.mark.asyncio
    async def test_no_plan_raises(self) -> None:
        state = PipelineState()
        with pytest.raises(ValueError, match="No workflow plan"):
            await run_validation_phase(state)


class TestAppCreation:
    def test_create_app(self) -> None:
        from workflow_conductor.app import create_app

        settings = ConductorSettings()
        app = create_app(settings)
        assert app.name == "workflow-conductor"
