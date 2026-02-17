"""Unit tests for the planning phase — download_commands extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.config import ConductorSettings


class TestDownloadCommandsExtraction:
    """Verify planning phase extracts download_commands from raw_plan."""

    @pytest.mark.asyncio
    async def test_extracts_download_commands_from_raw_plan(self) -> None:
        from workflow_conductor.models import PipelineState
        from workflow_conductor.phases.planning import run_planning_phase

        state = PipelineState()
        state.intent_classification = "1000genome"
        state.add_conversation("user", "Analyze chr11 EUR")
        settings = ConductorSettings()

        # Simulate Composer returning a plan with data_preparation commands
        plan_json = {
            "description": "1000 Genomes analysis",
            "chromosomes": ["11"],
            "populations": ["EUR"],
            "data_preparation": {
                "source_type": "ftp",
                "base_url": "https://ftp.1000genomes.ebi.ac.uk/...",
                "steps": [
                    {
                        "action": "download",
                        "commands": [
                            (
                                "curl -sL https://ftp.example.com"
                                "/ALL.chr11.vcf.gz"
                                " | gunzip > ALL.chr11.250000.vcf"
                            ),
                            (
                                "curl -sL https://ftp.example.com"
                                "/ALL.chr11.ann.vcf.gz"
                                " | gunzip > ALL.chr11.ann.vcf"
                            ),
                        ],
                        "output_file": "ALL.chr11.250000.vcf",
                    },
                ],
                "estimated_transfer_mb": 500.0,
                "use_remote_extraction": False,
            },
        }

        import json

        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = json.dumps(plan_json)
        history_mock = MagicMock()
        history_mock.get.return_value = []
        mock_llm.history = history_mock

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        assert len(result.workflow_plan.download_commands) == 2
        assert "chr11" in result.workflow_plan.download_commands[0]
        assert result.workflow_plan.data_preparation["source_type"] == "ftp"
        assert len(result.workflow_plan.data_preparation["steps"]) == 1

    @pytest.mark.asyncio
    async def test_no_data_preparation_yields_empty_commands(self) -> None:
        from workflow_conductor.models import PipelineState
        from workflow_conductor.phases.planning import run_planning_phase

        state = PipelineState()
        state.intent_classification = "1000genome"
        state.add_conversation("user", "Analyze chr1 EUR")
        settings = ConductorSettings()

        import json

        plan_json = {
            "description": "1000 Genomes analysis",
            "chromosomes": ["1"],
            "populations": ["EUR"],
        }

        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = json.dumps(plan_json)
        history_mock = MagicMock()
        history_mock.get.return_value = []
        mock_llm.history = history_mock

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        assert result.workflow_plan.download_commands == []
        assert result.workflow_plan.data_preparation == {}
