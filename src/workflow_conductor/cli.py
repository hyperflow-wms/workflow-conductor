"""CLI entry point for Workflow Conductor."""

from __future__ import annotations

import logging

import click

from workflow_conductor.ui.display import (
    display_error,
    display_pipeline_banner,
)


@click.group()
@click.version_option(package_name="workflow-conductor")
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default="INFO",
    help="Set logging level.",
)
def main(log_level: str) -> None:
    """Workflow Conductor — AI-powered genomics workflow orchestrator."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@main.command()
@click.argument(
    "prompt",
    default="Analyze EUR population, chromosome 22, small parallelism.",
)
@click.option("--dry-run", is_flag=True, help="Plan only, skip K8s deployment.")
@click.option("--auto-approve", is_flag=True, help="Skip validation gate prompts.")
def run(prompt: str, dry_run: bool, auto_approve: bool) -> None:
    """Run the conductor pipeline with a natural language prompt."""
    display_pipeline_banner(prompt, dry_run=dry_run)

    if dry_run:
        display_error("Dry-run mode: pipeline execution not yet implemented.")
    else:
        display_error("Full pipeline not yet implemented.")
