"""CLI entry point for Workflow Conductor."""

import click


@click.group()
@click.version_option(package_name="workflow-conductor")
def main() -> None:
    """Workflow Conductor — AI-powered genomics workflow orchestrator."""


@main.command()
@click.argument(
    "prompt",
    default="Analyze EUR population, chromosome 22, small parallelism.",
)
@click.option("--dry-run", is_flag=True, help="Plan only, skip K8s deployment.")
def run(prompt: str, dry_run: bool) -> None:
    """Run the conductor pipeline with a natural language prompt."""
    click.echo(f"Prompt: {prompt}")
    if dry_run:
        click.echo("[dry-run] Would plan workflow but skip deployment.")
    else:
        click.echo("[stub] Full pipeline not yet implemented.")
