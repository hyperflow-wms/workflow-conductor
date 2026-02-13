"""MCPApp initialization and pipeline skeleton."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp_agent.app import MCPApp
from mcp_agent.config import (
    MCPServerSettings,
    MCPSettings,
    Settings,
)

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


def create_app(settings: ConductorSettings) -> MCPApp:
    """Create and configure the MCPApp with Composer MCP server."""
    mcp_settings = Settings(
        execution_engine="asyncio",
        mcp=MCPSettings(
            servers={
                "workflow-composer": MCPServerSettings(
                    command=settings.workflow.composer_server_command,
                    args=settings.workflow.composer_server_args,
                ),
            },
        ),
    )
    return MCPApp(name="workflow-conductor", settings=mcp_settings)
