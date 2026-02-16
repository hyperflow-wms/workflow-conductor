"""Retry logic for transient K8s and infrastructure failures."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from workflow_conductor.k8s.cluster import ClusterError
from workflow_conductor.k8s.helm import HelmError
from workflow_conductor.k8s.kubectl import KubectlError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Exception types that indicate transient infrastructure failures
_TRANSIENT_TYPES: tuple[type[Exception], ...] = (
    KubectlError,
    HelmError,
    ClusterError,
    TimeoutError,
    ConnectionError,
    OSError,
)


def is_transient_error(exc: Exception) -> bool:
    """Check if an exception represents a transient failure worth retrying."""
    return isinstance(exc, _TRANSIENT_TYPES)


async def run_with_retry(
    coro_fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    backoff_base: float,
    phase: str,
    state: Any,
) -> T:
    """Run an async callable with exponential backoff on transient failures.

    Records each retry attempt in state.errors as a PipelineError.
    Non-transient exceptions are raised immediately without retry.
    """
    from workflow_conductor.models import PipelineError

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):  # 0..max_retries inclusive
        try:
            return await coro_fn()
        except Exception as exc:
            if not is_transient_error(exc):
                raise

            last_exc = exc
            if attempt >= max_retries:
                state.errors.append(
                    PipelineError(
                        phase=phase,
                        error_type=type(exc).__name__,
                        message=str(exc),
                        timestamp=datetime.now(UTC).isoformat(),
                        recoverable=False,
                        suggested_action=f"Max retries ({max_retries}) exhausted",
                    )
                )
                raise

            delay = backoff_base ** (attempt + 1)
            logger.warning(
                "Retry %d/%d for %s: %s (backoff %.1fs)",
                attempt + 1,
                max_retries,
                phase,
                exc,
                delay,
            )
            state.errors.append(
                PipelineError(
                    phase=phase,
                    error_type=type(exc).__name__,
                    message=str(exc),
                    timestamp=datetime.now(UTC).isoformat(),
                    recoverable=True,
                    suggested_action=f"Retrying ({attempt + 1}/{max_retries})",
                )
            )
            await asyncio.sleep(delay)

    # Should not reach here, but satisfy type checker
    raise last_exc  # type: ignore[misc]
