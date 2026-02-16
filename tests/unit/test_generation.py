"""Unit tests for the generation phase (deferred workflow generation)."""

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
    """State as it would be after provisioning completes."""
    return PipelineState(
        user_prompt="Analyze EUR chr22",
        current_phase=PipelinePhase.GENERATION,
        user_approved_plan=True,
        workflow_plan=WorkflowPlan(
            chromosomes=["22"],
            populations=["EUR"],
            parallelism=10,
            description="EUR chr22 analysis",
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


def _mock_agent_context(response: str) -> MagicMock:
    """Create a mock Agent with async context manager and LLM."""
    agent = MagicMock()
    llm = MagicMock()
    llm.generate_str = AsyncMock(return_value=response)
    # mcp-agent uses llm.history (SimpleMemory), not conversation_history
    history_mock = MagicMock()
    history_mock.get.return_value = []
    llm.history = history_mock
    agent.attach_llm = AsyncMock(return_value=llm)
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
        agent = _mock_agent_context(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            result = await run_generation_phase(state_after_provisioning, settings)
        assert result.workflow_json is not None
        assert result.workflow_json["name"] == "1000genome"
        assert len(result.workflow_json["processes"]) == 3

    @pytest.mark.asyncio
    async def test_includes_chromosome_data_in_prompt(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_context(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            await run_generation_phase(state_after_provisioning, settings)
        # Verify the LLM was called with chromosome data in the prompt
        call_args = agent.attach_llm.return_value.generate_str.call_args
        prompt = call_args.kwargs.get(
            "message", call_args.args[0] if call_args.args else ""
        )
        assert "chr22" in prompt or "494328" in prompt

    @pytest.mark.asyncio
    async def test_uses_context_replay(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        response = json.dumps(SAMPLE_WORKFLOW_JSON)
        agent = _mock_agent_context(response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            await run_generation_phase(state_after_provisioning, settings)
        # Verify the prompt includes context from planning
        call_args = agent.attach_llm.return_value.generate_str.call_args
        prompt = call_args.kwargs.get(
            "message", call_args.args[0] if call_args.args else ""
        )
        assert "EUR chr22" in prompt

    @pytest.mark.asyncio
    async def test_handles_non_json_response(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        agent = _mock_agent_context("Here is the workflow but not JSON")

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            result = await run_generation_phase(state_after_provisioning, settings)
        # Non-JSON response: workflow_json should be None
        assert result.workflow_json is None

    @pytest.mark.asyncio
    async def test_unsupported_provider_raises(
        self,
        state_after_provisioning: PipelineState,
    ) -> None:
        settings = ConductorSettings()
        settings.llm.default_provider = "unsupported"

        from workflow_conductor.phases.generation import run_generation_phase

        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            await run_generation_phase(state_after_provisioning, settings)

    @pytest.mark.asyncio
    async def test_extracts_json_from_google_function_response(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """Google Gemini returns function_call parts; JSON is in history."""
        # generate_str returns empty (only function_call parts, no text)
        agent = _mock_agent_context("")

        # Simulate Google-style history: tool result as function_response
        workflow_text = json.dumps(SAMPLE_WORKFLOW_JSON)
        result_part = MagicMock()
        result_part.text = workflow_text

        func_resp = MagicMock()
        func_resp.response = {"result": [result_part]}

        tool_part = MagicMock()
        tool_part.function_response = func_resp
        tool_part.text = None

        tool_msg = MagicMock()
        tool_msg.parts = [tool_part]
        tool_msg.role = "tool"

        llm = agent.attach_llm.return_value
        llm.history.get.return_value = [tool_msg]

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            result = await run_generation_phase(state_after_provisioning, settings)
        assert result.workflow_json is not None
        assert result.workflow_json["name"] == "1000genome"
        assert len(result.workflow_json["processes"]) == 3

    @pytest.mark.asyncio
    async def test_extracts_json_from_markdown(
        self,
        state_after_provisioning: PipelineState,
        settings: ConductorSettings,
    ) -> None:
        """LLM may wrap workflow JSON in markdown code blocks."""
        markdown_response = (
            "Here is the workflow:\n"
            "```json\n" + json.dumps(SAMPLE_WORKFLOW_JSON) + "\n```\n"
            "Use this to run."
        )
        agent = _mock_agent_context(markdown_response)

        with patch("workflow_conductor.phases.generation.Agent", return_value=agent):
            from workflow_conductor.phases.generation import (
                run_generation_phase,
            )

            result = await run_generation_phase(state_after_provisioning, settings)
        assert result.workflow_json is not None
        assert result.workflow_json["name"] == "1000genome"
