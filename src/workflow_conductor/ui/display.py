"""Rich display components for pipeline phases."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from workflow_conductor.models import (
    ExecutionSummary,
    PipelinePhase,
    PipelineState,
    WorkflowPlan,
)

console = Console()


def display_pipeline_banner(prompt: str, *, dry_run: bool = False) -> None:
    """Show the initial pipeline banner with the user prompt."""
    mode = "[bold yellow]DRY RUN[/bold yellow] " if dry_run else ""
    console.print(
        Panel(
            f"[bold]{prompt}[/bold]",
            title=f"{mode}[bold cyan]Workflow Conductor[/bold cyan]",
            subtitle="AI-powered genomics workflow orchestrator",
            border_style="cyan",
        )
    )


def display_phase_header(phase: PipelinePhase) -> None:
    """Show a phase transition header."""
    labels = {
        PipelinePhase.ROUTING: "Phase 1: Routing",
        PipelinePhase.PLANNING: "Phase 2: Planning",
        PipelinePhase.VALIDATION: "Phase 3: Validation",
        PipelinePhase.DEPLOYMENT: "Phase 4: Deployment",
        PipelinePhase.MONITORING: "Phase 5: Monitoring",
        PipelinePhase.COMPLETION: "Phase 6: Completion",
    }
    label = labels.get(phase, phase.value)
    console.rule(f"[bold blue]{label}[/bold blue]")


def display_workflow_plan(plan: WorkflowPlan) -> None:
    """Display a workflow plan in a Rich panel with a summary table."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")

    if plan.description:
        table.add_row("Description", plan.description)
    if plan.populations:
        table.add_row("Populations", ", ".join(plan.populations))
    if plan.chromosomes:
        table.add_row("Chromosomes", ", ".join(plan.chromosomes))
    if plan.parallelism is not None:
        table.add_row("Parallelism", str(plan.parallelism))
    if plan.estimated_data_size_gb > 0:
        table.add_row("Est. Data Size", f"{plan.estimated_data_size_gb:.1f} GB")

    console.print(
        Panel(
            table, title="[bold green]Workflow Plan[/bold green]", border_style="green"
        )
    )


def display_completion_summary(state: PipelineState) -> None:
    """Display the completion summary after pipeline finishes."""
    summary: ExecutionSummary | None = state.execution_summary

    table = Table(title="Execution Summary", show_header=True, header_style="bold")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Execution ID", state.execution_id or "N/A")
    table.add_row("Final Status", state.status.value)
    table.add_row("Namespace", state.namespace or "N/A")

    if summary:
        table.add_row("Total Tasks", str(summary.total_tasks))
        table.add_row("Completed", str(summary.completed_tasks))
        table.add_row("Failed", str(summary.failed_tasks))
        if summary.total_runtime_seconds > 0:
            mins = summary.total_runtime_seconds / 60
            table.add_row("Runtime", f"{mins:.1f} min")

    if state.phase_timings:
        for phase_name, duration in state.phase_timings.items():
            table.add_row(f"  {phase_name}", f"{duration:.1f}s")

    console.print(Panel(table, border_style="blue"))


def display_error(message: str, *, phase: str = "") -> None:
    """Display an error message."""
    prefix = f"\\[{phase}] " if phase else ""
    console.print(f"[bold red]{prefix}{message}[/bold red]")
