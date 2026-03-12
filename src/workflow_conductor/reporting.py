"""Experiment report generation for Euro-Par 2026 paper data collection.

Formats PipelineState into the markdown template defined in
paper/e2e-experiment-instructions.md.
"""

from __future__ import annotations

from workflow_conductor.models import PipelineState


def generate_experiment_report(state: PipelineState) -> str:
    """Generate a markdown experiment report from pipeline state."""
    lines: list[str] = []

    # Header
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
    lines.append(
        f"**LLM Model:** {state.llm_usage.get('planning', {}).get('model', 'N/A')}"
    )
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

    # Section 4: Deferred Generation
    lines.append("## 4. Deferred Generation (Experiment 3 data)")
    lines.append("")
    lines.append("### Variant Counts")
    lines.append("")
    lines.append("| Chromosome | Actual (Phase 5) |")
    lines.append("|------------|-------------------|")
    for cd in state.chromosome_data:
        lines.append(f"| chr{cd.chromosome} | {cd.row_count:,} |")
    lines.append("")

    if state.planning_estimates:
        lines.append("### Planning Estimates (Phase 2)")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k, v in state.planning_estimates.items():
            if k != "estimated_variants":
                lines.append(f"| {k} | {v} |")
        ev = state.planning_estimates.get("estimated_variants", {})
        if isinstance(ev, dict):
            for chrom, count in ev.items():
                lines.append(f"| estimated variants chr{chrom} | {count:,} |")
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
    lines.append("")

    # Section 6: Cluster Utilization
    lines.append("## 6. Cluster Utilization")
    lines.append("")
    if state.cluster_snapshots:
        # Show peak snapshot (last one captured, typically during peak execution)
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
        "{date}", state.execution_id[:8] if state.execution_id else "unknown"
    )

    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(report)

    return path
