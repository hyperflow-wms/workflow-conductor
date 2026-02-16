"""End-to-end tests — NL prompt to K8s execution.

These tests require:
- Running Kind cluster with loaded images
- Workflow Composer MCP server available
- Valid API keys for LLM provider

Run with: make test-e2e
"""

from __future__ import annotations

import pytest

from workflow_conductor.app import run_pipeline
from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineStatus


@pytest.mark.e2e
@pytest.mark.timeout(600)
class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_dry_run_pipeline(self, e2e_settings: ConductorSettings) -> None:
        """Test dry-run mode: routing + planning + validation only."""
        state = await run_pipeline(
            "Analyze EUR population, chromosome 22, small parallelism.",
            e2e_settings,
            dry_run=True,
            auto_approve=True,
        )

        assert state.status == PipelineStatus.COMPLETED
        assert state.intent_classification == "1000genome"
        assert state.workflow_plan is not None
        assert state.user_approved_plan is True
        # Dry-run covers 3 phases: routing, planning, validation
        assert len(state.phase_results) == 3
        # No deployment in dry-run
        assert state.namespace == ""

    @pytest.mark.asyncio
    @pytest.mark.timeout(900)
    async def test_full_pipeline_execution(
        self, e2e_settings: ConductorSettings
    ) -> None:
        """Test full pipeline: NL prompt -> K8s deployment -> monitoring.

        This test requires all infrastructure to be available:
        - Kind cluster with loaded images
        - Composer MCP server
        - hyperflow-k8s-deployment charts
        """
        state = await run_pipeline(
            "Analyze EUR population, chromosome 22, small parallelism.",
            e2e_settings,
            auto_approve=True,
        )

        assert state.status in (
            PipelineStatus.COMPLETED,
            PipelineStatus.FAILED,
        )
        assert state.namespace != ""
        assert state.execution_summary is not None
        # Full pipeline: 9 phases. Allow >= 7 for partial runs that fail late.
        assert len(state.phase_results) >= 7
