"""Generation phase: deferred workflow.json creation via Composer MCP.

Uses context replay (planner_history from planning phase) and actual
infrastructure measurements to call the Composer's generate_workflow tool.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM

from workflow_conductor.models import PipelinePhase, PipelineState
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)

GENERATION_INSTRUCTION = """You are a genomics workflow generation assistant.
You have access to the 1000 Genomes Workflow Composer MCP server.

Your task is to generate a complete HyperFlow workflow.json file. Follow these
steps IN ORDER:

1. Call estimate_variants for each chromosome listed below to get row counts.
   If no chromosome data is provided, use the chromosomes and populations
   from the research context.
2. Call generate_workflow with:
   - chromosome_data: array of {vcf_file, row_count, annotation_file} for
     each chromosome (use "ALL.chr{N}.phase3.vcf.gz" as vcf_file and
     "ALL.chr{N}.phase3.annotation.vcf.gz" as annotation_file)
   - populations: the population codes from the research plan
   - parallelism: "small" (unless specified otherwise)

You MUST call generate_workflow — do NOT return the workflow JSON yourself."""

LLM_FACTORIES: dict[str, type] = {
    "anthropic": AnthropicAugmentedLLM,
    "google": GoogleAugmentedLLM,
}


def _extract_workflow_json(response: str) -> dict[str, Any] | None:
    """Extract workflow JSON from LLM response.

    Handles: raw JSON, markdown code blocks, JSON embedded in text.
    Validates that extracted JSON contains both 'name' and 'processes' keys.
    """

    def _is_workflow(obj: dict[str, Any]) -> bool:
        return "processes" in obj and "name" in obj

    # Try direct JSON parse
    try:
        parsed: dict[str, Any] = json.loads(response)
        if _is_workflow(parsed):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if _is_workflow(parsed):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    # Try finding JSON object in response text (scan all { positions)
    for start in range(len(response)):
        if response[start] == "{":
            for end in range(len(response), start, -1):
                if response[end - 1] == "}":
                    try:
                        parsed = json.loads(response[start:end])
                        if _is_workflow(parsed):
                            return parsed
                    except (json.JSONDecodeError, TypeError):
                        continue

    return None


def _build_generation_prompt(state: PipelineState) -> str:
    """Build prompt for workflow generation with chromosome data and context."""
    parts: list[str] = []

    parts.append(state.synthesize_context_for_composer())

    if state.chromosome_data:
        chr_entries = []
        for cd in state.chromosome_data:
            chr_entries.append(
                f"  - chromosome: {cd.chromosome}, "
                f"vcf_file: {cd.vcf_file}, "
                f"row_count: {cd.row_count}, "
                f"annotation_file: {cd.annotation_file}"
            )
        parts.append(
            "Chromosome data for generate_workflow:\n" + "\n".join(chr_entries)
        )

    if state.workflow_plan:
        parts.append(
            f"Populations: {state.workflow_plan.populations}\n"
            f"Parallelism: {state.workflow_plan.parallelism}"
        )

    parts.append(
        "Call the generate_workflow tool with this chromosome data "
        "and return the complete workflow JSON."
    )

    return "\n\n".join(parts)


async def run_generation_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Generate workflow.json via Composer MCP using actual measurements.

    Uses context replay from planning phase and chromosome data from
    provisioning to produce the definitive workflow.json.
    """
    display_phase_header(PipelinePhase.GENERATION)

    # If the planning phase already produced workflow JSON (Gemini sometimes
    # calls generate_workflow in one shot), skip the redundant LLM call.
    if state.workflow_json is not None:
        logger.info(
            "Workflow JSON already present from planning phase (%d processes), "
            "skipping generation",
            len(state.workflow_json.get("processes", [])),
        )
        return state

    provider = settings.llm.default_provider
    llm_class = LLM_FACTORIES.get(provider)
    if llm_class is None:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    logger.info("Generating workflow with provider=%s", provider)

    generation_agent = Agent(
        name="generator",
        instruction=GENERATION_INSTRUCTION,
        server_names=["workflow-composer"],
    )

    async with generation_agent:
        llm = await generation_agent.attach_llm(llm_class)

        prompt = _build_generation_prompt(state)
        response = await llm.generate_str(
            message=prompt,
            request_params=RequestParams(max_iterations=10),
        )

    logger.debug("Generation response: %s", response)

    workflow_json = _extract_workflow_json(response)

    # Fallback: scan LLM history for tool results containing workflow JSON.
    # Google Gemini returns function_call parts (not text) so generate_str()
    # yields an empty string.  The actual workflow JSON lives inside
    # function_response parts stored in llm.history.
    if workflow_json is None and hasattr(llm, "history"):
        try:
            history = llm.history.get()
        except Exception:
            history = []

        for msg in reversed(history):
            if workflow_json is not None:
                break
            # Google: types.Content with .role and .parts
            if hasattr(msg, "parts") and msg.parts:
                for part in msg.parts:
                    # Tool result stored as function_response
                    fr = getattr(part, "function_response", None)
                    if fr and hasattr(fr, "response") and isinstance(fr.response, dict):
                        for val in fr.response.get("result", []):
                            text = getattr(val, "text", None)
                            if text:
                                workflow_json = _extract_workflow_json(text)
                                if workflow_json is not None:
                                    logger.info(
                                        "Extracted workflow JSON from tool result "
                                        "in conversation history"
                                    )
                                    break
                    # Also check plain text parts (model summary)
                    if workflow_json is None:
                        text = getattr(part, "text", None)
                        if text:
                            workflow_json = _extract_workflow_json(text)
            # Anthropic: dict-based messages
            elif isinstance(msg, dict):
                content = str(msg.get("content", ""))
                if content:
                    workflow_json = _extract_workflow_json(content)

    if workflow_json is not None:
        state.workflow_json = workflow_json
        logger.info(
            "Workflow generated: %d processes",
            len(workflow_json.get("processes", [])),
        )
    else:
        logger.warning("Could not extract workflow JSON from response")

    return state
