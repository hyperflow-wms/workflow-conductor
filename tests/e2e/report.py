"""E2E test report generator — detailed markdown reports."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflow_conductor.models import PipelineState

logger = logging.getLogger(__name__)

REPORT_DIR = Path("logs/e2e")


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m{secs:.0f}s"


def _job_table(jobs_data: dict[str, Any]) -> str:
    """Format K8s job details as a markdown table."""
    items = jobs_data.get("items", [])
    if not items:
        return "_No jobs found._\n"

    lines = ["| Job | Start | End | Duration | Status |"]
    lines.append("|---|---|---|---|---|")

    for job in sorted(items, key=lambda j: j.get("metadata", {}).get("name", "")):
        name = job.get("metadata", {}).get("name", "?")
        status = job.get("status", {})

        start_str = status.get("startTime", "")
        end_str = status.get("completionTime", "")

        # Parse times for duration
        start_short = start_str.split("T")[1][:8] if "T" in start_str else start_str
        end_short = end_str.split("T")[1][:8] if "T" in end_str else end_str

        duration = ""
        if start_str and end_str:
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                duration = _format_duration((end_dt - start_dt).total_seconds())
            except (ValueError, TypeError):
                duration = "?"

        # Determine job status
        conditions = status.get("conditions", [])
        job_status = "Running"
        for c in conditions:
            if c.get("status") == "True":
                job_status = c.get("type", "Unknown")
                break

        lines.append(
            f"| {name} | {start_short} | {end_short} | {duration} | {job_status} |"
        )

    return "\n".join(lines) + "\n"


def _file_table(file_sizes: dict[str, int | None]) -> str:
    """Format output file sizes as a markdown table."""
    if not file_sizes:
        return "_No files checked._\n"

    lines = ["| File | Size | Status |"]
    lines.append("|---|---|---|")

    for filename, size in sorted(file_sizes.items()):
        if size is None:
            lines.append(f"| {filename} | - | MISSING |")
        elif size == 0:
            lines.append(f"| {filename} | 0 B | EMPTY |")
        else:
            lines.append(f"| {filename} | {size:,} B | OK |")

    return "\n".join(lines) + "\n"


def _failure_analysis(
    state: PipelineState,
    error_message: str,
    file_sizes: dict[str, int | None],
    jobs_data: dict[str, Any] | None,
) -> str:
    """Analyze why the test failed and suggest possible solutions."""
    lines: list[str] = []
    lines.append("## Failure Analysis\n")

    # 1. Root cause from error message
    lines.append("### Error\n")
    lines.append(f"```\n{error_message}\n```\n")

    # 2. Failed phases
    failed_phases = [pr for pr in state.phase_results if pr.status == "failed"]
    if failed_phases:
        lines.append("### Failed Phases\n")
        for pr in failed_phases:
            lines.append(f"- **{pr.phase}**: {pr.error or 'no error message'}")
        lines.append("")

    # 3. Pipeline errors (structured)
    if state.errors:
        lines.append("### Pipeline Errors\n")
        for err in state.errors:
            lines.append(f"- **[{err.phase}] {err.error_type}**: {err.message}")
            if err.suggested_action:
                lines.append(f"  - Suggested action: {err.suggested_action}")
        lines.append("")

    # 4. Failed K8s jobs
    if jobs_data:
        failed_jobs = []
        for job in jobs_data.get("items", []):
            conditions = job.get("status", {}).get("conditions", [])
            for c in conditions:
                if c.get("type") == "Failed" and c.get("status") == "True":
                    name = job.get("metadata", {}).get("name", "?")
                    reason = c.get("reason", "Unknown")
                    msg = c.get("message", "")
                    failed_jobs.append((name, reason, msg))
                    break
        if failed_jobs:
            lines.append("### Failed K8s Jobs\n")
            lines.append("| Job | Reason | Message |")
            lines.append("|---|---|---|")
            for name, reason, msg in sorted(failed_jobs):
                lines.append(f"| {name} | {reason} | {msg[:120]} |")
            lines.append("")

    # 5. Missing/empty output files
    missing = [f for f, s in file_sizes.items() if s is None]
    empty = [f for f, s in file_sizes.items() if s == 0]
    if missing or empty:
        lines.append("### Output File Issues\n")
        if missing:
            lines.append(f"- **Missing files ({len(missing)}):** {', '.join(missing)}")
        if empty:
            lines.append(f"- **Empty files ({len(empty)}):** {', '.join(empty)}")
        lines.append("")

    # 6. Possible solutions
    lines.append("### Possible Solutions\n")

    if state.workflow_status == "failed":
        lines.append(
            "- **Workflow execution failed.** Check the engine logs and failed"
            " K8s jobs above for the root cause. Common issues:"
        )
        lines.append("  - Worker container crashes (OOM, missing dependencies)")
        lines.append("  - Input data issues (corrupt VCF, missing chromosomes)")
        lines.append(
            "  - Resource pressure (insufficient CPU/memory for parallel jobs)"
        )
        lines.append("")

    if failed_phases:
        phase_names = [pr.phase for pr in failed_phases]
        if "provisioning" in phase_names:
            lines.append(
                "- **Provisioning failed.** Check cluster availability,"
                " Helm chart compatibility, and image pull status."
            )
        if "monitoring" in phase_names:
            lines.append(
                "- **Monitoring failed.** The workflow may have timed out."
                " Consider increasing `monitor_timeout` or checking for"
                " stalled jobs."
            )
        if "planning" in phase_names:
            lines.append(
                "- **Planning failed.** The LLM may have produced an invalid"
                " plan. Check MCP server logs and Composer tool responses."
            )
        lines.append("")

    if missing:
        lines.append(
            "- **Missing output files** indicate that some workflow stages"
            " did not complete. Cross-reference with failed jobs above to"
            " identify which pipeline step (sifting, individuals, frequency,"
            " mutation) failed."
        )
        lines.append("")

    return "\n".join(lines)


def generate_report(
    *,
    case_id: str,
    state: PipelineState,
    passed: bool,
    file_sizes: dict[str, int | None],
    jobs_data: dict[str, Any] | None = None,
    columns_txt: str = "",
    engine_logs: str = "",
    error_message: str = "",
) -> Path:
    """Generate a detailed markdown test report and write to disk.

    Returns the path to the generated report file.
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    report_path = REPORT_DIR / f"{case_id}-{timestamp}.md"

    status_str = "PASSED" if passed else "FAILED"
    total_runtime = sum(state.phase_timings.values())

    lines: list[str] = []

    # Header
    lines.append(f"# E2E Test Report: {case_id}\n")
    lines.append(f"**Date:** {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append(f"**Status:** {status_str}")
    lines.append(f"**Execution ID:** {state.execution_id}")
    lines.append(f"**Namespace:** {state.namespace}")
    lines.append(f"**Prompt:** {state.user_prompt}")
    if error_message:
        lines.append(f"**Error:** {error_message}")
    lines.append("")

    # Summary table
    lines.append("## Summary\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total runtime | {_format_duration(total_runtime)} |")
    lines.append(f"| Total tasks | {state.total_task_count} |")
    lines.append(f"| Completed tasks | {state.task_completion_count} |")
    failed = max(0, state.total_task_count - state.task_completion_count)
    lines.append(f"| Failed tasks | {failed} |")
    lines.append(f"| Workflow status | {state.workflow_status} |")
    lines.append(f"| Pipeline status | {state.status} |")
    lines.append("")

    # Failure analysis (only for failed tests)
    if not passed and error_message:
        lines.append(_failure_analysis(state, error_message, file_sizes, jobs_data))
        lines.append("")

    # Infrastructure
    if state.infrastructure:
        lines.append("## Infrastructure\n")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Nodes | {state.infrastructure.node_count} |")
        lines.append(f"| vCPUs | {state.infrastructure.available_vcpus} |")
        lines.append(f"| Memory | {state.infrastructure.memory_gb:.1f} GB |")
        lines.append(f"| K8s version | {state.infrastructure.k8s_version} |")
        lines.append("")

    # Phase Timings
    lines.append("## Phase Timings\n")
    lines.append("| # | Phase | Duration | Status |")
    lines.append("|---|---|---|---|")
    for i, pr in enumerate(state.phase_results, 1):
        dur = _format_duration(pr.duration_seconds)
        lines.append(f"| {i} | {pr.phase} | {dur} | {pr.status} |")
    lines.append("")

    # Workflow Plan
    if state.workflow_plan:
        plan = state.workflow_plan
        lines.append("## Workflow Plan\n")
        lines.append(f"- **Chromosomes:** {', '.join(plan.chromosomes)}")
        lines.append(f"- **Populations:** {', '.join(plan.populations)}")
        lines.append(f"- **Parallelism:** {plan.parallelism}")
        lines.append(f"- **Estimated data size:** {plan.estimated_data_size_gb} GB")
        lines.append(f"- **Description:** {plan.description}")
        lines.append("")

    # Chromosome Data
    if state.chromosome_data:
        lines.append("## Chromosome Data\n")
        lines.append("| Chr | VCF File | Row Count | Annotation |")
        lines.append("|---|---|---|---|")
        for cd in state.chromosome_data:
            lines.append(
                f"| {cd.chromosome} | {cd.vcf_file}"
                f" | {cd.row_count:,} | {cd.annotation_file} |"
            )
        lines.append("")

    # K8s Job Details
    if jobs_data:
        lines.append("## K8s Job Details\n")
        lines.append(_job_table(jobs_data))
        lines.append("")

    # Output Files
    lines.append("## Output Files\n")
    lines.append(_file_table(file_sizes))
    lines.append("")

    # Artifacts
    lines.append("## Artifacts\n")

    # workflow.json
    if state.workflow_json:
        lines.append("### workflow.json\n")
        lines.append("```json")
        lines.append(json.dumps(state.workflow_json, indent=2))
        lines.append("```\n")

    # columns.txt
    if columns_txt:
        lines.append("### columns.txt\n")
        lines.append("```")
        lines.append(columns_txt)
        lines.append("```\n")

    # Engine logs
    if engine_logs:
        lines.append("## Engine Logs (last 200 lines)\n")
        lines.append("```")
        lines.append(engine_logs)
        lines.append("```\n")

    report_content = "\n".join(lines)
    report_path.write_text(report_content)
    logger.info("Report written to %s", report_path)

    return report_path
