"""Phase 2: Workflow planning via Composer MCP server.

Uses mcp-agent's Agent + AugmentedLLM to call the Workflow Composer's
MCP tools (plan_workflow, estimate_variants, etc.) and produce a WorkflowPlan.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM

from workflow_conductor.models import PipelineState, WorkflowPlan

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)

COMPOSER_INSTRUCTION = """You are a genomics workflow planning assistant.
You have access to tools from the 1000 Genomes Workflow Composer MCP server.

Available tools:
- plan_workflow: Create a workflow plan from a research intent
- estimate_variants: Estimate variant counts and data sizes for given regions
- generate_workflow: Generate a complete workflow.json
- list_known_regions: List available chromosomal regions
- list_populations: List available population groups

Use these tools to understand the user's research intent and create
a comprehensive workflow plan. Always call plan_workflow first, then
estimate_variants for the relevant regions."""

LLM_FACTORIES: dict[str, type] = {
    "anthropic": AnthropicAugmentedLLM,
    "google": GoogleAugmentedLLM,
}


def _parse_plan_from_response(response: str) -> dict[str, Any]:
    """Try to extract structured plan data from the LLM response."""
    try:
        parsed: dict[str, Any] = json.loads(response)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return {"description": response}


async def run_planning_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Run the planning phase using Composer MCP tools via mcp-agent.

    Creates an Agent connected to the workflow-composer MCP server,
    attaches an LLM (Anthropic or Google based on config), and
    generates a workflow plan.
    """
    provider = settings.llm.default_provider
    llm_class = LLM_FACTORIES.get(provider)
    if llm_class is None:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    logger.info("Planning with provider=%s", provider)

    composer_agent = Agent(
        name="composer",
        instruction=COMPOSER_INSTRUCTION,
        server_names=["workflow-composer"],
    )

    async with composer_agent:
        llm = await composer_agent.attach_llm(llm_class)

        prompt = state.synthesize_context_for_composer()
        response = await llm.generate_str(
            message=f"Plan a workflow for: {prompt}",
            request_params=RequestParams(max_iterations=5),
        )

    logger.debug("Planning response: %s", response)

    plan_data = _parse_plan_from_response(response)
    state.workflow_plan = WorkflowPlan(
        description=plan_data.get("description", response[:500]),
        chromosomes=plan_data.get("chromosomes", []),
        populations=plan_data.get("populations", []),
        parallelism=plan_data.get("parallelism"),
        estimated_data_size_gb=plan_data.get("estimated_data_size_gb", 0.0),
        raw_plan=plan_data,
    )

    # Capture conversation history for context replay in later phases
    if hasattr(llm, "conversation_history"):
        state.planner_history = list(llm.conversation_history)

    state.add_conversation("assistant", response)

    return state
