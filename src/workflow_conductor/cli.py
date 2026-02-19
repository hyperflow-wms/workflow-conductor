"""CLI entry point for Workflow Conductor."""

from __future__ import annotations

import asyncio
import logging

import click

from workflow_conductor.app import run_pipeline
from workflow_conductor.config import ConductorSettings


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
@click.option(
    "--dry-run",
    is_flag=True,
    help="Plan only, skip K8s deployment.",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Skip validation gate prompts.",
)
@click.option(
    "--demo",
    is_flag=True,
    help="Demo mode with explanations and pauses.",
)
@click.option(
    "--no-pause",
    is_flag=True,
    help="Skip 'Press Enter' pauses in demo mode (non-interactive).",
)
@click.option(
    "--no-teardown",
    is_flag=True,
    help="Keep K8s namespace and resources after completion.",
)
def run(
    prompt: str,
    dry_run: bool,
    auto_approve: bool,
    demo: bool,
    no_pause: bool,
    no_teardown: bool,
) -> None:
    """Run the conductor pipeline with a natural language prompt."""
    settings = ConductorSettings()
    if no_teardown:
        settings.no_teardown = True
    asyncio.run(
        run_pipeline(
            prompt,
            settings,
            dry_run=dry_run,
            auto_approve=auto_approve,
            demo=demo,
            no_pause=no_pause,
        )
    )
