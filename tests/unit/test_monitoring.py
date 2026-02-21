"""Unit tests for monitoring phase — mocked Sentinel."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineState


def _make_summary(
    completed: int = 0,
    failed: int = 0,
    total: int = 0,
    exit_code: int | None = None,
    timed_out: bool = False,
    mass_failure: bool = False,
) -> "WatchSummary":
    from execution_sentinel.models import WatchSummary

    return WatchSummary(
        completed_tasks=completed,
        failed_tasks=failed,
        total_tasks=total,
        exit_code=exit_code,
        timed_out=timed_out,
        mass_failure=mass_failure,
    )


def _make_sentinel_mock(summary: "WatchSummary") -> MagicMock:
    """Build a mock Sentinel instance that returns the given summary."""
    instance = MagicMock()
    instance.watch = AsyncMock(return_value=summary)
    instance.reports = asyncio.Queue()
    instance.kubectl = MagicMock()
    instance.kubectl.logs = AsyncMock(return_value="")
    return instance


_DISPLAY_PATCHES = [
    "workflow_conductor.phases.monitoring.display_phase_header",
    "workflow_conductor.phases.monitoring.display_sentinel_banner",
    "workflow_conductor.phases.monitoring.display_report",
    "workflow_conductor.phases.monitoring.display_completion_summary",
]


class TestMonitoringPhase:
    @pytest.mark.asyncio
    async def test_detects_workflow_completion(self) -> None:
        """Sentinel returns exit_code=0 → workflow 'completed'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="engine-pod-1")
        settings = ConductorSettings()
        summary = _make_summary(completed=5, total=5, exit_code=0)

        with ExitStack() as stack:
            MockSentinel = stack.enter_context(
                patch("workflow_conductor.phases.monitoring.Sentinel")
            )
            for p in _DISPLAY_PATCHES:
                stack.enter_context(patch(p))
            MockSentinel.return_value = _make_sentinel_mock(summary)
            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "completed"
        assert result.task_completion_count == 5
        assert result.total_task_count == 5

    @pytest.mark.asyncio
    async def test_detects_workflow_failure(self) -> None:
        """Sentinel returns exit_code=1 → workflow 'failed'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="engine-pod-1")
        settings = ConductorSettings()
        summary = _make_summary(completed=2, failed=1, total=5, exit_code=1)

        with ExitStack() as stack:
            MockSentinel = stack.enter_context(
                patch("workflow_conductor.phases.monitoring.Sentinel")
            )
            for p in _DISPLAY_PATCHES:
                stack.enter_context(patch(p))
            MockSentinel.return_value = _make_sentinel_mock(summary)
            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "failed"

    @pytest.mark.asyncio
    async def test_detects_mass_failure(self) -> None:
        """Sentinel sets mass_failure=True → workflow 'failed'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="engine-pod-1")
        settings = ConductorSettings()
        summary = _make_summary(completed=1, failed=4, total=5, mass_failure=True)

        with ExitStack() as stack:
            MockSentinel = stack.enter_context(
                patch("workflow_conductor.phases.monitoring.Sentinel")
            )
            for p in _DISPLAY_PATCHES:
                stack.enter_context(patch(p))
            MockSentinel.return_value = _make_sentinel_mock(summary)
            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "failed"

    @pytest.mark.asyncio
    async def test_detects_timeout(self) -> None:
        """Sentinel sets timed_out=True → workflow 'timeout'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="engine-pod-1")
        settings = ConductorSettings()
        summary = _make_summary(timed_out=True)

        with ExitStack() as stack:
            MockSentinel = stack.enter_context(
                patch("workflow_conductor.phases.monitoring.Sentinel")
            )
            for p in _DISPLAY_PATCHES:
                stack.enter_context(patch(p))
            MockSentinel.return_value = _make_sentinel_mock(summary)
            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "timeout"

    @pytest.mark.asyncio
    async def test_requires_engine_pod(self) -> None:
        """Missing engine_pod_name → ValueError before Sentinel is created."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="")
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="engine pod not set"):
            await run_monitoring_phase(state, settings)

    @pytest.mark.asyncio
    async def test_task_counts_from_summary(self) -> None:
        """completed_tasks and total_tasks are mapped from WatchSummary."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="engine-pod-1")
        settings = ConductorSettings()
        summary = _make_summary(completed=3, total=7, exit_code=0)

        with ExitStack() as stack:
            MockSentinel = stack.enter_context(
                patch("workflow_conductor.phases.monitoring.Sentinel")
            )
            for p in _DISPLAY_PATCHES:
                stack.enter_context(patch(p))
            MockSentinel.return_value = _make_sentinel_mock(summary)
            result = await run_monitoring_phase(state, settings)

        assert result.task_completion_count == 3
        assert result.total_task_count == 7

    @pytest.mark.asyncio
    async def test_sentinel_constructed_with_correct_namespace(self) -> None:
        """Sentinel receives the namespace from PipelineState."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="my-namespace", engine_pod_name="engine-pod-1")
        settings = ConductorSettings()
        summary = _make_summary(exit_code=0)

        with ExitStack() as stack:
            MockSentinel = stack.enter_context(
                patch("workflow_conductor.phases.monitoring.Sentinel")
            )
            for p in _DISPLAY_PATCHES:
                stack.enter_context(patch(p))
            mock_instance = _make_sentinel_mock(summary)
            MockSentinel.return_value = mock_instance
            await run_monitoring_phase(state, settings)

        context_arg = MockSentinel.call_args[0][0]
        assert context_arg.namespace == "my-namespace"
