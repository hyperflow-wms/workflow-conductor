"""Phase 2: Workflow planning via Composer MCP server.

Uses mcp-agent's Agent + AugmentedLLM to call the Workflow Composer's
MCP tools (plan_workflow, estimate_variants, etc.) and produce a WorkflowPlan.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
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


def _extract_plan_data_from_history(llm: Any) -> dict[str, Any]:
    """Extract structured plan data from LLM tool calls in history.

    Google Gemini's generate_str() only returns text parts, dropping
    tool call arguments and results.  This scans llm.history for
    plan_workflow function_call args (chromosomes, populations) and
    function_response text (the full plan markdown with embedded JSON).
    """
    result: dict[str, Any] = {}
    if not hasattr(llm, "history"):
        return result
    try:
        history = llm.history.get()
    except Exception:
        return result

    for msg in history:
        if not hasattr(msg, "parts") or not msg.parts:
            continue
        for part in msg.parts:
            # Extract plan_workflow args → chromosomes, populations
            # (name may be server-prefixed by mcp-agent)
            fc = getattr(part, "function_call", None)
            fc_name = getattr(fc, "name", "") or "" if fc else ""
            if fc_name.endswith("plan_workflow"):
                args = getattr(fc, "args", {}) or {}
                if args.get("chromosomes"):
                    result["chromosomes"] = list(args["chromosomes"])
                if args.get("populations"):
                    result["populations"] = list(args["populations"])
                if args.get("focus"):
                    result["focus"] = args["focus"]

            # Extract tool responses
            fr = getattr(part, "function_response", None)
            if fr and hasattr(fr, "response") and isinstance(fr.response, dict):
                for val in fr.response.get("result", []):
                    text = getattr(val, "text", None)
                    if not text:
                        continue
                    # plan_workflow returns markdown with embedded JSON
                    if "## Workflow Plan" in text:
                        result.setdefault("plan_text", text)
                        json_match = re.search(
                            r"```json\s*\n(.*?)\n```", text, re.DOTALL
                        )
                        if json_match:
                            try:
                                plan_json = json.loads(json_match.group(1))
                                result["raw_plan"] = plan_json
                                if "description" not in result:
                                    result["description"] = plan_json.get(
                                        "description", ""
                                    )
                                dp = plan_json.get("data_preparation", {})
                                if dp.get("estimated_transfer_mb"):
                                    result["estimated_data_size_gb"] = (
                                        dp["estimated_transfer_mb"] / 1024.0
                                    )
                                hints = plan_json.get("execution_hints", {})
                                if hints.get("recommended_parallelism"):
                                    result["parallelism"] = hints[
                                        "recommended_parallelism"
                                    ]
                            except (json.JSONDecodeError, TypeError):
                                pass
                    # generate_workflow may also be called during planning
                    # (Gemini sometimes does the full pipeline in one shot)
                    if "## Generated Workflow" in text:
                        from workflow_conductor.phases.generation import (
                            _extract_workflow_json,
                        )

                        wf = _extract_workflow_json(text)
                        if wf is not None:
                            result["workflow_json"] = wf
                            logger.info(
                                "Workflow JSON found in planning history "
                                "(%d processes)",
                                len(wf.get("processes", [])),
                            )

    logger.info(
        "Extracted from history: chromosomes=%s populations=%s",
        result.get("chromosomes", []),
        result.get("populations", []),
    )
    return result


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

    # Primary: try parsing the text response as JSON
    plan_data = _parse_plan_from_response(response)

    # Fallback: extract structured data from tool calls in LLM history
    # (needed for Google Gemini where generate_str only returns text parts)
    history_data = _extract_plan_data_from_history(llm)
    for key in ("chromosomes", "populations", "parallelism", "estimated_data_size_gb"):
        if not plan_data.get(key) and history_data.get(key):
            plan_data[key] = history_data[key]
    if history_data.get("description") and plan_data.get("description", "") == response:
        plan_data["description"] = history_data["description"]
    if history_data.get("raw_plan") and not plan_data.get("raw_plan"):
        plan_data["raw_plan"] = history_data["raw_plan"]
    if history_data.get("plan_text"):
        plan_data.setdefault("plan_text", history_data["plan_text"])

    # If Gemini called generate_workflow during planning, store it now
    # so the generation phase can skip the redundant LLM call.
    if history_data.get("workflow_json"):
        state.workflow_json = history_data["workflow_json"]
        logger.info(
            "Workflow JSON captured during planning (%d processes)",
            len(state.workflow_json.get("processes", [])),
        )

    state.workflow_plan = WorkflowPlan(
        description=plan_data.get("description", response[:500]),
        chromosomes=plan_data.get("chromosomes", []),
        populations=plan_data.get("populations", []),
        parallelism=plan_data.get("parallelism"),
        estimated_data_size_gb=plan_data.get("estimated_data_size_gb", 0.0),
        raw_plan=plan_data.get("raw_plan", plan_data),
    )

    # Capture conversation history for context replay in later phases.
    # mcp-agent stores history in llm.history (SimpleMemory), not
    # conversation_history.
    if hasattr(llm, "history"):
        with contextlib.suppress(Exception):
            state.planner_history = [
                {"role": getattr(m, "role", "unknown"), "content": str(m)}
                for m in llm.history.get()
            ]

    state.add_conversation("assistant", response)

    return state
