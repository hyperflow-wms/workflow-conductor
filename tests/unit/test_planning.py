"""Unit tests for the planning phase — download_commands extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.config import ConductorSettings


def _make_mock_llm(response_json: str, history: list | None = None) -> AsyncMock:
    """Build a mock LLM returning *response_json* with optional history."""
    mock_llm = AsyncMock()
    mock_llm.generate_str.return_value = response_json
    history_mock = MagicMock()
    history_mock.get.return_value = history or []
    mock_llm.history = history_mock
    return mock_llm


def _make_mock_agent(mock_llm: AsyncMock) -> MagicMock:
    """Wrap *mock_llm* in an async-context-manager mock Agent."""
    mock_agent = MagicMock()
    mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
    mock_agent.__aexit__ = AsyncMock(return_value=False)
    mock_agent.attach_llm = AsyncMock(return_value=mock_llm)
    return mock_agent


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

        mock_llm = _make_mock_llm(json.dumps(plan_json))
        mock_agent = _make_mock_agent(mock_llm)

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

        mock_llm = _make_mock_llm(json.dumps(plan_json))
        mock_agent = _make_mock_agent(mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        assert result.workflow_plan.download_commands == []
        assert result.workflow_plan.data_preparation == {}


class TestChromosomeInference:
    """Verify chromosome inference from download commands and workflow JSON."""

    def test_empty_history_yields_no_chromosomes(self) -> None:
        """Empty LLM history produces no inferred chromosomes."""
        from workflow_conductor.phases.planning import (
            _extract_plan_data_from_history,
        )

        mock_llm = MagicMock()
        history_mock = MagicMock()
        history_mock.get.return_value = []
        mock_llm.history = history_mock

        result = _extract_plan_data_from_history(mock_llm)
        assert result.get("chromosomes") is None
        assert result.get("download_commands") is None

    def test_infers_from_raw_plan_commands(self) -> None:
        """Chromosomes inferred from raw_plan data_preparation commands."""
        from workflow_conductor.phases.planning import (
            _extract_plan_data_from_history,
        )

        # Build a mock LLM whose history contains a plan_workflow response
        # with data_preparation commands referencing chr17
        plan_text = (
            "## Workflow Plan\n\n```json\n"
            '{"description": "BRCA1 analysis", '
            '"data_preparation": {"steps": [{"commands": ['
            '"tabix -h s3://bucket/ALL.chr17.vcf.gz chr17:41196312-41277500"'
            "]}]}}\n```"
        )

        # Simulate Google Gemini history structure
        fr_part = MagicMock()
        fr_part.function_call = None
        fr_response = MagicMock()
        fr_response.response = {"result": [MagicMock(text=plan_text)]}
        fr_part.function_response = fr_response

        msg = MagicMock()
        msg.parts = [fr_part]

        mock_llm = MagicMock()
        history_mock = MagicMock()
        history_mock.get.return_value = [msg]
        mock_llm.history = history_mock

        result = _extract_plan_data_from_history(mock_llm)

        assert result.get("chromosomes") == ["17"]
        assert len(result.get("download_commands", [])) == 1
        assert "chr17" in result["download_commands"][0]

    def test_infers_from_workflow_json_process_names(self) -> None:
        """Chromosomes inferred from workflow JSON process names."""
        from workflow_conductor.phases.planning import (
            _extract_plan_data_from_history,
        )

        # Simulate a generate_workflow response with chr17 process names
        wf_text = (
            "## Generated Workflow\n\n```json\n"
            '{"name": "1000genome", "processes": ['
            '{"name": "chr17-individuals-1", "type": "dataflow"},'
            '{"name": "chr17-sifting-1", "type": "dataflow"}'
            "]}\n```"
        )

        fr_part = MagicMock()
        fr_part.function_call = None
        fr_response = MagicMock()
        fr_response.response = {"result": [MagicMock(text=wf_text)]}
        fr_part.function_response = fr_response

        msg = MagicMock()
        msg.parts = [fr_part]

        mock_llm = MagicMock()
        history_mock = MagicMock()
        history_mock.get.return_value = [msg]
        mock_llm.history = history_mock

        result = _extract_plan_data_from_history(mock_llm)

        assert result.get("chromosomes") == ["17"]
        assert "workflow_json" in result

    @pytest.mark.asyncio
    async def test_inferred_chromosomes_carry_download_commands(self) -> None:
        """When chromosomes are inferred from history, download_commands
        from the same history should also be available in the plan."""
        from workflow_conductor.models import PipelineState
        from workflow_conductor.phases.planning import run_planning_phase

        state = PipelineState()
        state.intent_classification = "1000genome"
        state.add_conversation("user", "Analyze BRCA1 in British population")
        settings = ConductorSettings()

        # LLM text response is not valid JSON (Gemini often returns prose)
        mock_llm = _make_mock_llm("I've created a workflow for BRCA1...")

        # Simulate history with plan_workflow response containing
        # data_preparation commands for chr17
        plan_text = (
            "## Workflow Plan\n\n```json\n"
            '{"description": "BRCA1 GBR analysis", '
            '"data_preparation": {"steps": [{"commands": ['
            '"tabix -h https://ftp.example.com/ALL.chr17.vcf.gz '
            'chr17:41196312-41277500 > ALL.chr17.250000.vcf"'
            "]}]}}\n```"
        )

        fr_part = MagicMock()
        fr_part.function_call = None
        fr_response = MagicMock()
        fr_response.response = {"result": [MagicMock(text=plan_text)]}
        fr_part.function_response = fr_response

        msg = MagicMock()
        msg.parts = [fr_part]

        history_mock = MagicMock()
        history_mock.get.return_value = [msg]
        mock_llm.history = history_mock

        mock_agent = _make_mock_agent(mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        assert result.workflow_plan.chromosomes == ["17"]
        assert len(result.workflow_plan.download_commands) == 1
        assert "chr17" in result.workflow_plan.download_commands[0]

    @pytest.mark.asyncio
    async def test_workflow_json_only_infers_chromosomes(self) -> None:
        """When only workflow_json has chromosome info (no raw_plan
        commands), chromosomes are inferred but download_commands
        remain empty — this is the edge case the fallback handles."""
        from workflow_conductor.models import PipelineState
        from workflow_conductor.phases.planning import run_planning_phase

        state = PipelineState()
        state.intent_classification = "1000genome"
        state.add_conversation("user", "Analyze BRCA1 in British population")
        settings = ConductorSettings()

        mock_llm = _make_mock_llm("Created BRCA1 workflow.")

        # Only generate_workflow in history (no plan_workflow)
        wf_text = (
            "## Generated Workflow\n\n```json\n"
            '{"name": "1000genome", "processes": ['
            '{"name": "chr17-individuals-1", "type": "dataflow"},'
            '{"name": "chr17-sifting-1", "type": "dataflow"}'
            "]}\n```"
        )

        fr_part = MagicMock()
        fr_part.function_call = None
        fr_response = MagicMock()
        fr_response.response = {"result": [MagicMock(text=wf_text)]}
        fr_part.function_response = fr_response

        msg = MagicMock()
        msg.parts = [fr_part]

        history_mock = MagicMock()
        history_mock.get.return_value = [msg]
        mock_llm.history = history_mock

        mock_agent = _make_mock_agent(mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        # Chromosomes inferred from process names
        assert result.workflow_plan.chromosomes == ["17"]
        # No download commands available (no raw_plan data_preparation)
        assert result.workflow_plan.download_commands == []
        # Workflow JSON is intentionally discarded during planning —
        # generation phase will regenerate with actual row counts
        assert result.workflow_json is None

    @pytest.mark.asyncio
    async def test_history_fallback_when_text_raw_plan_lacks_commands(
        self,
    ) -> None:
        """When the text response has a raw_plan without data_preparation
        but the history has download_commands, the fallback populates them."""
        import json

        from workflow_conductor.models import PipelineState
        from workflow_conductor.phases.planning import run_planning_phase

        state = PipelineState()
        state.intent_classification = "1000genome"
        state.add_conversation("user", "Analyze BRCA1 in British population")
        settings = ConductorSettings()

        # Text response parses as JSON with raw_plan but NO data_preparation
        text_plan = {
            "description": "BRCA1 analysis",
            "raw_plan": {"description": "BRCA1 workflow", "analysis_steps": []},
        }
        mock_llm = _make_mock_llm(json.dumps(text_plan))

        # History has plan_workflow response with data_preparation commands
        plan_text = (
            "## Workflow Plan\n\n```json\n"
            '{"description": "BRCA1 analysis", '
            '"data_preparation": {"steps": [{"commands": ['
            '"tabix -h https://ftp.example.com/ALL.chr17.vcf.gz '
            'chr17:41196312-41277500 > ALL.chr17.250000.vcf"'
            "]}]}}\n```"
        )

        fr_part = MagicMock()
        fr_part.function_call = None
        fr_response = MagicMock()
        fr_response.response = {"result": [MagicMock(text=plan_text)]}
        fr_part.function_response = fr_response

        msg = MagicMock()
        msg.parts = [fr_part]

        history_mock = MagicMock()
        history_mock.get.return_value = [msg]
        mock_llm.history = history_mock

        mock_agent = _make_mock_agent(mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            result = await run_planning_phase(state, settings)

        assert result.workflow_plan is not None
        # Chromosomes inferred from history download commands
        assert result.workflow_plan.chromosomes == ["17"]
        # Download commands populated via history fallback
        assert len(result.workflow_plan.download_commands) == 1
        assert "chr17" in result.workflow_plan.download_commands[0]
