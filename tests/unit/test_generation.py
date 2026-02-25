"""Unit tests for the generation phase (deterministic MCP tool call)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import (
    ChromosomeData,
    InfrastructureMeasurements,
    PipelinePhase,
    PipelineState,
    WorkflowPlan,
)


@pytest.fixture
def settings() -> ConductorSettings:
    return ConductorSettings()


@pytest.fixture
def state_after_provisioning() -> PipelineState:
    """State as it would be after data_preparation completes."""
    return PipelineState(
        user_prompt="Analyze EUR chr22",
        current_phase=PipelinePhase.GENERATION,
        user_approved_plan=True,
        workflow_plan=WorkflowPlan(
            chromosomes=["22"],
            populations=["EUR"],
            parallelism=10,
            description="EUR chr22 analysis",
            raw_plan={
                "parameters_used": {
                    "populations": ["EUR"],
                    "ind_jobs": 10,
                },
            },
        ),
        infrastructure=InfrastructureMeasurements(
            namespace="wf-20260216-120000",
            node_count=2,
            available_vcpus=8,
            memory_gb=16.0,
            k8s_version="v1.31.0",
        ),
        chromosome_data=[
            ChromosomeData(
                vcf_file="ALL.chr22.phase3.vcf.gz",
                row_count=494328,
                annotation_file="ALL.chr22.phase3.annotation.vcf.gz",
                chromosome="22",
            ),
        ],
        planner_history=[
            {"role": "user", "content": "Plan a workflow for EUR chr22"},
            {"role": "assistant", "content": "Here is a plan..."},
        ],
    )


SAMPLE_WORKFLOW_JSON = {
    "name": "1000genome",
    "version": "1.0.0",
    "processes": [
        {"name": "individuals_chr22_1", "type": "dataflow"},
        {"name": "sifting_chr22", "type": "dataflow"},
        {"name": "frequency_EUR_chr22", "type": "dataflow"},
    ],
    "signals": [],
    "ins": [],
    "outs": [],
}


def _mock_agent_with_tool_result(
    response_text: str, is_error: bool = False
) -> MagicMock:
    """Create a mock Agent that returns a tool call result."""
    agent = MagicMock()

    # Build CallToolResult-like mock
    text_part = MagicMock()
    text_part.text = response_text

    tool_result = MagicMock()
    tool_result.isError = is_error
    tool_result.content = [text_part]

    agent.call_tool = AsyncMock(return_value=tool_result)
    agent.__aenter__ = AsyncMock(return_value=agent)
    agent.__aexit__ = AsyncMock(return_value=False)
    return agent


class TestGenerationPhase:
    @pytest.mark.asyncio
    async def test_produces_workflow_json(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_with_tool_result(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            result = await run_generation_phase(state_after_provisioning, settings)
        assert result.workflow_json is not None
        assert result.workflow_json["name"] == "1000genome"
        assert len(result.workflow_json["processes"]) == 3

    @pytest.mark.asyncio
    async def test_passes_exact_chromosome_data(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_with_tool_result(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            await run_generation_phase(state_after_provisioning, settings)

        # Verify call_tool was called with exact chromosome data
        call_args = agent.call_tool.call_args
        tool_args = call_args.kwargs.get("arguments", {})
        assert tool_args["chromosome_data"] == [
            {
                "vcf_file": "ALL.chr22.phase3.vcf.gz",
                "row_count": 494328,
                "annotation_file": "ALL.chr22.phase3.annotation.vcf.gz",
            }
        ]
        assert tool_args["populations"] == ["EUR"]
        assert tool_args["ind_jobs"] == 10

    @pytest.mark.asyncio
    async def test_uses_parallelism_small_fallback(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """When raw_plan has no ind_jobs, falls back to parallelism=small."""
        state_after_provisioning.workflow_plan = WorkflowPlan(
            chromosomes=["22"],
            populations=["EUR"],
            parallelism=10,
            description="EUR chr22 analysis",
            raw_plan={},  # No parameters_used
        )
        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_with_tool_result(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            await run_generation_phase(state_after_provisioning, settings)

        call_args = agent.call_tool.call_args
        tool_args = call_args.kwargs.get("arguments", {})
        assert "ind_jobs" not in tool_args
        assert tool_args["parallelism"] == "small"

    @pytest.mark.asyncio
    async def test_mcp_tool_error_raises(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        agent = _mock_agent_with_tool_result("Something went wrong", is_error=True)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            with pytest.raises(RuntimeError, match="generate_workflow MCP tool failed"):
                await run_generation_phase(state_after_provisioning, settings)

    @pytest.mark.asyncio
    async def test_invalid_response_raises(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        agent = _mock_agent_with_tool_result("Here is the workflow but not JSON")

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            with pytest.raises(RuntimeError, match="Could not extract workflow JSON"):
                await run_generation_phase(state_after_provisioning, settings)

    @pytest.mark.asyncio
    async def test_requires_chromosome_data(
        self,
        settings: ConductorSettings,
    ) -> None:
        state = PipelineState(
            user_prompt="test",
            workflow_plan=WorkflowPlan(populations=["EUR"]),
            chromosome_data=[],
        )

        from workflow_conductor.phases.generation import run_generation_phase

        with pytest.raises(ValueError, match="no chromosome data"):
            await run_generation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_requires_workflow_plan(
        self,
        settings: ConductorSettings,
    ) -> None:
        state = PipelineState(
            user_prompt="test",
            chromosome_data=[
                ChromosomeData(
                    vcf_file="test.vcf",
                    row_count=100,
                    annotation_file="test.ann.vcf",
                    chromosome="1",
                ),
            ],
        )

        from workflow_conductor.phases.generation import run_generation_phase

        with pytest.raises(ValueError, match="no workflow plan"):
            await run_generation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_requires_populations(
        self,
        settings: ConductorSettings,
    ) -> None:
        state = PipelineState(
            user_prompt="test",
            workflow_plan=WorkflowPlan(populations=[]),
            chromosome_data=[
                ChromosomeData(
                    vcf_file="test.vcf",
                    row_count=100,
                    annotation_file="test.ann.vcf",
                    chromosome="1",
                ),
            ],
        )

        from workflow_conductor.phases.generation import run_generation_phase

        with pytest.raises(ValueError, match="no populations"):
            await run_generation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_extracts_json_from_markdown(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """MCP tool returns markdown with embedded JSON code block."""
        markdown_response = (
            "## Generated Workflow\n\n"
            "```json\n" + json.dumps(SAMPLE_WORKFLOW_JSON) + "\n```\n"
        )
        agent = _mock_agent_with_tool_result(markdown_response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            result = await run_generation_phase(state_after_provisioning, settings)
        assert result.workflow_json is not None
        assert result.workflow_json["name"] == "1000genome"

    @pytest.mark.asyncio
    async def test_passes_vcf_header_when_available(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """vcf_header from data_preparation is passed to generate_workflow."""
        state_after_provisioning.vcf_header = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG00096"
        )
        columns_content = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG00096"
        )
        response = (
            f"### columns.txt (1 individuals)\n```\n{columns_content}\n```\n\n"
            f"### Workflow JSON\n```json\n{json.dumps(SAMPLE_WORKFLOW_JSON)}\n```"
        )
        agent = _mock_agent_with_tool_result(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import run_generation_phase

            await run_generation_phase(state_after_provisioning, settings)

        call_args = agent.call_tool.call_args
        tool_args = call_args.kwargs.get("arguments", {})
        assert tool_args["vcf_header"] == state_after_provisioning.vcf_header

    @pytest.mark.asyncio
    async def test_no_vcf_header_omits_param(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """When vcf_header is empty, it's not passed to MCP tool."""
        state_after_provisioning.vcf_header = ""
        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_with_tool_result(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import run_generation_phase

            await run_generation_phase(state_after_provisioning, settings)

        call_args = agent.call_tool.call_args
        tool_args = call_args.kwargs.get("arguments", {})
        assert "vcf_header" not in tool_args

    @pytest.mark.asyncio
    async def test_extracts_columns_txt(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """columns.txt is extracted from MCP response when vcf_header provided."""
        state_after_provisioning.vcf_header = "#CHROM\tPOS\tFORMAT\tHG00096"
        columns_content = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG00096"
        )
        markdown_response = (
            "### columns.txt (1 individuals)\n"
            f"```\n{columns_content}\n```\n\n"
            "### Workflow JSON\n"
            f"```json\n{json.dumps(SAMPLE_WORKFLOW_JSON)}\n```"
        )
        agent = _mock_agent_with_tool_result(markdown_response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import run_generation_phase

            result = await run_generation_phase(state_after_provisioning, settings)

        assert result.columns_txt == columns_content
        assert result.workflow_json is not None

    @pytest.mark.asyncio
    async def test_extracts_population_files(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """Population files are extracted from MCP response."""
        state_after_provisioning.vcf_header = "#CHROM\tPOS\tFORMAT\tHG00096"
        markdown_response = (
            "### columns.txt (1 individuals)\n"
            "```\n#CHROM\tPOS\tFORMAT\tHG00096\n```\n\n"
            "### Population Files\n\n"
            "**EUR** (1 individuals):\n"
            "```\nHG00096\nHG00097\n```\n\n"
            "### Workflow JSON\n"
            f"```json\n{json.dumps(SAMPLE_WORKFLOW_JSON)}\n```"
        )
        agent = _mock_agent_with_tool_result(markdown_response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import run_generation_phase

            result = await run_generation_phase(state_after_provisioning, settings)

        assert "EUR" in result.population_files
        assert "HG00096" in result.population_files["EUR"]
        assert "HG00097" in result.population_files["EUR"]

    @pytest.mark.asyncio
    async def test_raises_when_vcf_header_but_no_columns_txt(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """RuntimeError when vcf_header provided but response has no columns.txt."""
        state_after_provisioning.vcf_header = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG00096"
        )
        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_with_tool_result(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import run_generation_phase

            with pytest.raises(RuntimeError, match="no columns.txt found"):
                await run_generation_phase(state_after_provisioning, settings)

    @pytest.mark.asyncio
    async def test_extracts_pop_files_without_vcf_header(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """Population files extracted even when vcf_header is empty."""
        state_after_provisioning.vcf_header = ""
        markdown_response = (
            "### Population Files\n\n"
            "**GBR** (91 individuals):\n"
            "```\nHG00096\nHG00097\n```\n\n"
            "### Workflow JSON\n"
            f"```json\n{json.dumps(SAMPLE_WORKFLOW_JSON)}\n```"
        )
        agent = _mock_agent_with_tool_result(markdown_response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import run_generation_phase

            result = await run_generation_phase(state_after_provisioning, settings)

        assert "GBR" in result.population_files
        assert "HG00096" in result.population_files["GBR"]

    @pytest.mark.asyncio
    async def test_overwrites_planning_phase_workflow(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """Generation always regenerates, even if planning captured a workflow."""
        # Simulate Gemini having generated workflow during planning
        state_after_provisioning.workflow_json = {
            "name": "old-from-planning",
            "processes": [{"name": "stale"}],
        }

        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_with_tool_result(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            result = await run_generation_phase(state_after_provisioning, settings)
        # Should overwrite with fresh generation, not keep stale planning workflow
        assert result.workflow_json is not None
        assert result.workflow_json["name"] == "1000genome"
        assert len(result.workflow_json["processes"]) == 3
