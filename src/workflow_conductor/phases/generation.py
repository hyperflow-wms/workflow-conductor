"""Generation phase: deterministic workflow.json creation via Composer MCP.

Calls the Composer's generate_workflow MCP tool directly (no LLM) with
exact chromosome data from the data_preparation phase.  This mirrors
the upstream CLI path: `g1kwf generate --data-csv data.csv`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from mcp_agent.agents.agent import Agent

from workflow_conductor.models import PipelinePhase, PipelineState
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


def _extract_columns_txt(response: str) -> str:
    """Extract columns.txt content from MCP tool response.

    Looks for a markdown section like:
        ### columns.txt (91 individuals)
        ```
        #CHROM  POS  ...
        ```
    """
    match = re.search(
        r"###\s*columns\.txt[^\n]*\n```[^\n]*\n(.*?)\n```",
        response,
        re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _extract_population_files(response: str) -> dict[str, str]:
    """Extract population file contents from MCP tool response.

    Looks for a "Population Files" section containing entries like:
        **GBR** (91 individuals):
        ```
        HG00096
        HG00097
        ...
        ```

    Only parses entries after a "Population Files" header to avoid matching
    other bold text in the response.
    """
    # Find the Population Files section
    section_match = re.search(
        r"#+\s*Population\s+Files\s*\n(.*)",
        response,
        re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return {}

    section_text = section_match.group(1)

    result: dict[str, str] = {}
    for match in re.finditer(
        r"\*\*(\w+)\*\*\s*\([^)]*\):\s*\n```[^\n]*\n(.*?)\n```",
        section_text,
        re.DOTALL,
    ):
        pop_name = match.group(1)
        content = match.group(2).strip()
        if content:
            result[pop_name] = content
    return result


def _extract_workflow_json(response: str) -> dict[str, Any] | None:
    """Extract workflow JSON from MCP tool response text.

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


async def run_generation_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Generate workflow.json by calling Composer MCP tool directly.

    This is a deterministic transformation: exact chromosome data (filenames
    and row counts from data_preparation) + populations + parallelism
    are passed programmatically to the generate_workflow MCP tool.
    No LLM is involved — eliminates non-determinism from bugs where the
    LLM would call estimate_variants, use wrong filenames, or change
    parallelism.
    """
    display_phase_header(PipelinePhase.GENERATION)

    if not state.chromosome_data:
        raise ValueError(
            "Cannot generate workflow: no chromosome data "
            "(data_preparation phase required)"
        )
    if not state.workflow_plan:
        raise ValueError(
            "Cannot generate workflow: no workflow plan (planning phase required)"
        )

    # Build chromosome_data argument from actual scanned VCF data
    chromosome_data = [
        {
            "vcf_file": cd.vcf_file,
            "row_count": cd.row_count,
            "annotation_file": cd.annotation_file,
        }
        for cd in state.chromosome_data
    ]

    # Get populations from the planning phase
    populations = state.workflow_plan.populations
    if not populations:
        raise ValueError("Cannot generate workflow: no populations in workflow plan")

    # Get ind_jobs from plan's parameters_used (matches upstream data.csv path),
    # falling back to "small" preset (10) if not available.
    raw_plan = state.workflow_plan.raw_plan or {}
    params_used = raw_plan.get("parameters_used", {})
    ind_jobs = params_used.get("ind_jobs")

    # Build MCP tool arguments
    tool_args: dict[str, Any] = {
        "chromosome_data": chromosome_data,
        "populations": populations,
    }
    if state.vcf_header:
        tool_args["vcf_header"] = state.vcf_header
    if ind_jobs is not None:
        tool_args["ind_jobs"] = ind_jobs
    else:
        tool_args["parallelism"] = "small"

    logger.info(
        "Generating workflow: %d chromosomes, populations=%s, %s",
        len(chromosome_data),
        populations,
        f"ind_jobs={ind_jobs}" if ind_jobs else "parallelism=small",
    )
    for cd in chromosome_data:
        logger.info(
            "  %s: %d rows (%s)",
            cd["vcf_file"],
            cd["row_count"],
            cd["annotation_file"],
        )

    # Call generate_workflow MCP tool directly — no LLM involved
    composer_agent = Agent(
        name="generator",
        instruction="",
        server_names=["workflow-composer"],
    )

    async with composer_agent:
        result = await composer_agent.call_tool(
            name="generate_workflow",
            arguments=tool_args,
        )

    # Extract workflow JSON from MCP tool response
    if result.isError:
        error_text = (
            getattr(result.content[0], "text", str(result.content[0]))
            if result.content
            else "unknown error"
        )
        raise RuntimeError(f"generate_workflow MCP tool failed: {error_text}")

    response_text = "\n".join(
        getattr(part, "text", "") for part in (result.content or [])
    )
    logger.debug("MCP tool response length: %d chars", len(response_text))

    workflow_json = _extract_workflow_json(response_text)

    if workflow_json is not None:
        state.workflow_json = workflow_json
        logger.info(
            "Workflow generated: %d processes",
            len(workflow_json.get("processes", [])),
        )
    else:
        raise RuntimeError(
            "Could not extract workflow JSON from generate_workflow response"
        )

    # Extract columns.txt when vcf_header was provided
    if state.vcf_header:
        columns_txt = _extract_columns_txt(response_text)
        if columns_txt:
            state.columns_txt = columns_txt
            col_count = len(columns_txt.split("\t"))
            logger.info(
                "Extracted columns.txt: %d fields (%d samples)",
                col_count,
                max(0, col_count - 9),
            )
        else:
            raise RuntimeError(
                "vcf_header was provided but no columns.txt found in "
                "generate_workflow response — workers require columns.txt"
            )

    # Extract population files unconditionally — MCP returns them regardless
    # of vcf_header, and workers need them for frequency analysis
    pop_files = _extract_population_files(response_text)
    if pop_files:
        state.population_files = pop_files
        for name, content in pop_files.items():
            logger.info(
                "Extracted population file: %s (%d individuals)",
                name,
                len(content.splitlines()),
            )

    return state
