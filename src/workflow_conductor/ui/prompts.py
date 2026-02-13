"""Rich confirmation prompts for validation gates."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt

from workflow_conductor.models import UserResponse

console = Console()


def prompt_validation_gate(*, auto_approve: bool = False) -> UserResponse:
    """Prompt user to approve, refine, or abort the workflow plan.

    If auto_approve is True, returns approval immediately without prompting.
    """
    if auto_approve:
        console.print("[dim]Auto-approve enabled, skipping validation gate.[/dim]")
        return UserResponse(action="approve")

    console.print()
    console.print("[bold]What would you like to do?[/bold]")
    console.print("  [green]approve[/green]  - Proceed with deployment")
    console.print("  [yellow]refine[/yellow]   - Modify the plan")
    console.print("  [red]abort[/red]    - Cancel the pipeline")
    console.print()

    action = Prompt.ask(
        "Choose action",
        choices=["approve", "refine", "abort"],
        default="approve",
    )

    feedback = ""
    if action == "refine":
        feedback = Prompt.ask("Describe your modifications")

    return UserResponse(action=action, feedback=feedback)
