"""Unit tests for full pipeline wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.app import run_pipeline
from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineStatus, UserResponse


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_dry_run_stops_after_validation(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = '{"description": "test"}'
        mock_llm.conversation_history = []

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            state = await run_pipeline(
                "test prompt",
                ConductorSettings(),
                dry_run=True,
                auto_approve=True,
            )

        assert state.status == PipelineStatus.COMPLETED
        assert state.intent_classification == "1000genome"
        assert state.workflow_plan is not None
        assert state.user_approved_plan is True
        # Deployment should NOT have run
        assert state.namespace == ""

    @pytest.mark.asyncio
    async def test_abort_stops_pipeline(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = '{"description": "test"}'
        mock_llm.conversation_history = []

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        with (
            patch(
                "workflow_conductor.phases.planning.Agent",
                return_value=mock_agent,
            ),
            patch(
                "workflow_conductor.phases.validation.prompt_validation_gate",
                return_value=UserResponse(action="abort"),
            ),
        ):
            state = await run_pipeline(
                "test prompt",
                ConductorSettings(),
            )

        assert state.status == PipelineStatus.ABORTED
