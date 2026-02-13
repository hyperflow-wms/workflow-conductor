"""Phase 1: Intent classification and routing.

MVP: Hardcoded to 1000genome workflow. Future stages will add LLM-based
intent classification to support multiple workflow types.
"""

from __future__ import annotations

import logging

from workflow_conductor.models import PipelineState

logger = logging.getLogger(__name__)

SUPPORTED_DOMAIN = "1000genome"


async def run_routing_phase(state: PipelineState) -> PipelineState:
    """Route the user prompt to the appropriate workflow domain.

    MVP implementation: always routes to 1000genome.
    """
    logger.info("Routing prompt to domain: %s", SUPPORTED_DOMAIN)
    state.intent_classification = SUPPORTED_DOMAIN
    return state
