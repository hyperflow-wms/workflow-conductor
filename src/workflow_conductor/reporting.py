"""Experiment report generation for Euro-Par 2026 paper data collection.

Formats PipelineState into the markdown template defined in
paper/e2e-experiment-instructions.md.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from workflow_conductor import __version__
from workflow_conductor.models import PipelineState

# Approximate pricing per million tokens (USD), snapshot as of 2026-03.
_LLM_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash-preview-04-17": {"input": 0.15, "output": 0.60},
}


def _estimate_cost(usage: dict[str, Any]) -> str:
    """Estimate USD cost from token counts and model ID."""
    model = usage.get("model", "")
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    if inp == 0 and out == 0:
        return "N/A"
    pricing = _LLM_PRICING.get(model)
    if not pricing:
        return "N/A (unknown model)"
    cost = (inp * pricing["input"] + out * pricing["output"]) / 1_000_000
    return f"${cost:.4f}"


def _get_git_commit() -> str:
    """Get current git commit hash, or 'unknown'."""
    try:
        # Pin to package root so git works regardless of process cwd
        package_root = Path(__file__).resolve().parent.parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(package_root),
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return "unknown"


def _format_bytes(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    if size_bytes == 0:
        return "N/A"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def generate_experiment_report(state: PipelineState) -> str:
    """Generate a markdown experiment report from pipeline state."""
    lines: list[str] = []

    # Header
    git_commit = _get_git_commit()
    lines.append(f"# E2E Experiment Results — {state.execution_id}")
    lines.append("")
    lines.append(f"**Date:** {state.created_at}")
    lines.append(f'**Query:** "{state.user_prompt}"')

    # Cluster info
    if state.infrastructure:
        lines.append(
            f"**Cluster:** {state.infrastructure.node_count} nodes, "
            f"{state.infrastructure.available_vcpus} vCPUs, "
            f"{state.infrastructure.memory_gb:.0f} GB RAM"
        )
    planning_usage = state.llm_usage.get("planning", {})
    lines.append(f"**LLM Model:** {planning_usage.get('model', 'N/A')}")
    lines.append(f"**Conductor Version:** {__version__} ({git_commit})")
    lines.append(f"**Namespace:** {state.namespace}")
    lines.append(f"**Status:** {state.status.value}")
    lines.append("")

    # Section 1: Intent Interpretation
    lines.append("## 1. Intent Interpretation")
    lines.append("")
    wp = state.workflow_plan
    if wp:
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| Populations | {', '.join(wp.populations)} |")
        lines.append(f"| Chromosomes | {', '.join(wp.chromosomes)} |")
        lines.append(f"| Parallelism | {wp.parallelism} |")
        lines.append(f"| Description | {wp.description} |")
    lines.append("")

    # Section 2: Phase Timing
    lines.append("## 2. Phase Timing (seconds)")
    lines.append("")
    lines.append("| Phase | Duration (s) |")
    lines.append("|-------|-------------|")
    total = 0.0
    for phase_name in [
        "routing",
        "planning",
        "validation",
        "provisioning",
        "data_preparation",
        "generation",
        "approval",
        "deployment",
        "monitoring",
        "completion",
    ]:
        dur = state.phase_timings.get(phase_name, 0.0)
        total += dur
        label = phase_name.replace("_", " ").title()
        note = ""
        if phase_name in ("validation", "approval"):
            note = " (auto-approve)" if dur == 0 else ""
        lines.append(f"| {label} | {dur:.1f}{note} |")
    lines.append(f"| **Total** | **{total:.1f}** |")
    lines.append("")

    # Grouped timings
    llm_time = state.phase_timings.get("planning", 0) + state.phase_timings.get(
        "generation", 0
    )
    infra_time = (
        state.phase_timings.get("provisioning", 0)
        + state.phase_timings.get("data_preparation", 0)
        + state.phase_timings.get("deployment", 0)
    )
    exec_time = state.phase_timings.get("monitoring", 0)
    overhead = state.phase_timings.get("routing", 0) + state.phase_timings.get(
        "completion", 0
    )

    lines.append("### Grouped")
    lines.append("")
    lines.append("| Category | Duration (s) | % of total |")
    lines.append("|----------|-------------|------------|")
    for cat, dur in [
        ("LLM (Planning+Generation)", llm_time),
        ("Infrastructure (Provisioning+DataPrep+Deployment)", infra_time),
        ("Execution (Monitoring)", exec_time),
        ("Overhead (Routing+Completion)", overhead),
    ]:
        pct = (dur / total * 100) if total > 0 else 0
        lines.append(f"| {cat} | {dur:.1f} | {pct:.1f}% |")
    lines.append("")

    # Section 3: Workflow Metrics
    lines.append("## 3. Workflow Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    if wp:
        lines.append(f"| Populations | {', '.join(wp.populations)} |")
        lines.append(f"| Chromosomes | {', '.join(wp.chromosomes)} |")
        lines.append(f"| Parallelism (J) | {wp.parallelism} |")
    wf = state.workflow_json or {}
    processes = wf.get("processes", [])
    signals = wf.get("signals", [])
    lines.append(f"| Total processes | {len(processes)} |")
    lines.append(f"| Total signals | {len(signals)} |")
    lines.append(f"| K8s jobs completed | {state.task_completion_count} |")
    failed = max(0, state.total_task_count - state.task_completion_count)
    lines.append(f"| K8s jobs failed | {failed} |")

    # Task type breakdown
    type_counts: dict[str, int] = {}
    for proc in processes:
        fun = proc.get("fun", "")
        type_counts[fun] = type_counts.get(fun, 0) + 1
    for tt, count in sorted(type_counts.items()):
        lines.append(f"| &nbsp;&nbsp;{tt} | {count} |")
    lines.append("")

    # Section 4: Deferred Generation (Experiment 3 data)
    lines.append("## 4. Deferred Generation (Experiment 3 data)")
    lines.append("")

    # 4a: Variant counts — estimated vs actual comparison
    lines.append("### Variant Counts")
    lines.append("")
    ev = (
        state.planning_estimates.get("estimated_variants", {})
        if state.planning_estimates
        else {}
    )
    has_estimates = isinstance(ev, dict) and len(ev) > 0
    if has_estimates:
        lines.append(
            "| Chromosome | Estimated (Phase 2) | Actual (Phase 5) | Delta (%) |"
        )
        lines.append(
            "|------------|--------------------|-------------------|-----------|"
        )
        for cd in state.chromosome_data:
            estimated = ev.get(cd.chromosome)
            if estimated is not None:
                delta_pct = (
                    ((cd.row_count - estimated) / estimated * 100)
                    if estimated > 0
                    else 0
                )
                lines.append(
                    f"| chr{cd.chromosome} | {estimated:,} "
                    f"| {cd.row_count:,} | {delta_pct:+.1f}% |"
                )
            else:
                lines.append(f"| chr{cd.chromosome} | N/A | {cd.row_count:,} | — |")
    else:
        lines.append("| Chromosome | Actual (Phase 5) |")
        lines.append("|------------|-------------------|")
        for cd in state.chromosome_data:
            lines.append(f"| chr{cd.chromosome} | {cd.row_count:,} |")
    lines.append("")

    # 4b: Data sizes
    has_sizes = any(cd.file_size_bytes > 0 for cd in state.chromosome_data)
    if has_sizes:
        lines.append("### Data Sizes")
        lines.append("")
        lines.append("| Chromosome | Downloaded Size |")
        lines.append("|------------|----------------|")
        total_bytes = 0
        for cd in state.chromosome_data:
            total_bytes += cd.file_size_bytes
            lines.append(
                f"| chr{cd.chromosome} | {_format_bytes(cd.file_size_bytes)} |"
            )
        lines.append(f"| **Total** | **{_format_bytes(total_bytes)}** |")
        lines.append("")

    # 4c: Task count comparison
    est_tasks = (
        state.planning_estimates.get("estimated_tasks")
        if state.planning_estimates
        else None
    )
    actual_tasks = len(processes)
    if est_tasks is not None and actual_tasks > 0:
        lines.append("### Task Count Adjustment")
        lines.append("")
        lines.append("| Metric | Advisory (Phase 2) | Final (Phase 6) | Delta |")
        lines.append("|--------|-------------------|-----------------|-------|")
        est_tasks_int = int(est_tasks)
        delta = actual_tasks - est_tasks_int
        lines.append(f"| Total tasks | {est_tasks_int} | {actual_tasks} | {delta:+d} |")
        est_par = state.planning_estimates.get("estimated_parallelism")
        actual_par = wp.parallelism if wp else None
        if est_par is not None and actual_par is not None:
            par_delta = int(actual_par) - int(est_par)
            lines.append(
                f"| Parallelism (J) | {int(est_par)} | {int(actual_par)} "
                f"| {par_delta:+d} |"
            )
        lines.append("")
    elif state.planning_estimates:
        lines.append("### Planning Estimates (Phase 2)")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k, v in state.planning_estimates.items():
            if k == "estimated_variants" and isinstance(v, dict):
                for chrom, count in v.items():
                    lines.append(f"| estimated variants chr{chrom} | {count:,} |")
            else:
                lines.append(f"| {k} | {v} |")
        lines.append("")

    # Section 5: LLM Usage
    lines.append("## 5. LLM Usage")
    lines.append("")
    lines.append("| Metric | Planning (Phase 2) | Monitoring (Phase 9) |")
    lines.append("|--------|-------------------|---------------------|")
    pu = state.llm_usage.get("planning", {})
    mu = state.llm_usage.get("monitoring", {})
    lines.append(f"| Model | {pu.get('model', 'N/A')} | {mu.get('model', 'N/A')} |")
    p_in = pu.get("input_tokens", "N/A")
    m_in = mu.get("input_tokens", "N/A")
    lines.append(f"| Input tokens | {p_in} | {m_in} |")
    p_out = pu.get("output_tokens", "N/A")
    m_out = mu.get("output_tokens", "N/A")
    lines.append(f"| Output tokens | {p_out} | {m_out} |")
    lines.append(
        f"| Tool calls | {pu.get('tool_calls', 'N/A')} | {mu.get('api_calls', 'N/A')} |"
    )
    lines.append(f"| Latency (ms) | {pu.get('latency_ms', 'N/A')} | N/A |")
    # Cost estimation
    p_cost = _estimate_cost(pu)
    m_cost = _estimate_cost(mu)
    lines.append(f"| Est. cost ($) | {p_cost} | {m_cost} |")
    lines.append("")

    # Note: Generation (Phase 6) is deterministic MCP tool call, no LLM
    lines.append(
        "*Phase 6 (Generation) uses a deterministic MCP tool call "
        "— no LLM tokens consumed.*"
    )
    lines.append("")

    # Section 6: Cluster Utilization
    lines.append("## 6. Cluster Utilization")
    lines.append("")
    if state.cluster_snapshots:
        # Show peak snapshot (last one captured)
        peak = state.cluster_snapshots[-1]
        lines.append(f"**Snapshot at:** {peak.get('timestamp', 'N/A')}")
        lines.append(f"**Total snapshots captured:** {len(state.cluster_snapshots)}")
        lines.append("")
        lines.append("```")
        lines.append("# kubectl top nodes")
        lines.append(peak.get("nodes", "N/A"))
        lines.append("")
        lines.append("# kubectl top pods")
        lines.append(peak.get("pods", "N/A"))
        lines.append("```")
    else:
        lines.append("No cluster snapshots captured.")
    lines.append("")

    # Section 7: Raw Artifacts
    lines.append("## 7. Raw Artifacts")
    lines.append("")
    lines.append(f"- Namespace: `{state.namespace}`")
    lines.append(f"- Execution ID: `{state.execution_id}`")
    lines.append(f"- Conductor version: `{__version__}` (`{git_commit}`)")
    lines.append("")

    return "\n".join(lines)


def write_experiment_report(
    state: PipelineState,
    output_path: str,
) -> str:
    """Generate and write the experiment report to disk.

    Returns the path where the report was written.
    """
    report = generate_experiment_report(state)

    # Resolve placeholders in path
    path = output_path.replace(
        "{date}",
        state.execution_id[:8] if state.execution_id else "unknown",
    )

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(report)

    return path
