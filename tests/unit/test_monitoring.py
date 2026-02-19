"""Unit tests for monitoring phase — mocked K8s operations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineState
from workflow_conductor.phases.monitoring import _count_jobs


def _pod_status(exit_code: int, reason: str = "Completed") -> dict:
    """Build a pod JSON with a terminated hyperflow container."""
    return {
        "status": {
            "containerStatuses": [
                {
                    "name": "hyperflow",
                    "state": {
                        "terminated": {
                            "exitCode": exit_code,
                            "reason": reason,
                        }
                    },
                }
            ]
        }
    }


def _pod_running() -> dict:
    """Build a pod JSON with a running hyperflow container."""
    return {
        "status": {
            "containerStatuses": [
                {
                    "name": "hyperflow",
                    "state": {"running": {"startedAt": "2026-01-01T00:00:00Z"}},
                }
            ]
        }
    }


def _jobs_response(completed: int = 0, failed: int = 0, active: int = 0) -> dict:
    """Build a jobs list JSON."""
    items: list[dict] = []
    for i in range(completed):
        items.append(
            {
                "metadata": {"name": f"job-ok-{i}"},
                "status": {"conditions": [{"type": "Complete", "status": "True"}]},
            }
        )
    for i in range(failed):
        items.append(
            {
                "metadata": {"name": f"job-fail-{i}"},
                "status": {"conditions": [{"type": "Failed", "status": "True"}]},
            }
        )
    for i in range(active):
        items.append(
            {
                "metadata": {"name": f"job-active-{i}"},
                "status": {"active": 1},
            }
        )
    return {"items": items}


class TestCountJobs:
    def test_all_completed(self) -> None:
        items = _jobs_response(completed=3)["items"]
        assert _count_jobs(items) == (3, 3, 0)

    def test_mixed(self) -> None:
        items = _jobs_response(completed=2, failed=1, active=1)["items"]
        assert _count_jobs(items) == (4, 2, 1)

    def test_empty(self) -> None:
        assert _count_jobs([]) == (0, 0, 0)


class TestMonitoringPhase:
    @pytest.mark.asyncio
    async def test_detects_engine_completion(self) -> None:
        """Engine exit code 0 → workflow 'completed'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        def mock_get_json(resource: str, **kwargs: object) -> dict:
            if resource.startswith("pod/"):
                return _pod_status(exit_code=0)
            return _jobs_response(completed=5)

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.get_json = AsyncMock(side_effect=mock_get_json)
            kubectl.logs = AsyncMock(return_value="done")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "completed"
        assert result.total_task_count == 5
        assert result.task_completion_count == 5

    @pytest.mark.asyncio
    async def test_detects_engine_failure(self) -> None:
        """Engine exit code 1 → workflow 'failed'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        def mock_get_json(resource: str, **kwargs: object) -> dict:
            if resource.startswith("pod/"):
                return _pod_status(exit_code=1, reason="Error")
            return _jobs_response(completed=2, failed=1)

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.get_json = AsyncMock(side_effect=mock_get_json)
            kubectl.logs = AsyncMock(return_value="error")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "failed"

    @pytest.mark.asyncio
    async def test_waits_while_engine_running(self) -> None:
        """Monitoring keeps polling while engine is still running."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        # First call: engine running, 2/5 jobs done
        # Second call: engine terminated, 5/5 jobs done
        call_count = 0

        def mock_get_json(resource: str, **kwargs: object) -> dict:
            nonlocal call_count
            call_count += 1
            if resource.startswith("pod/"):
                if call_count <= 2:  # first iteration (pod + jobs)
                    return _pod_running()
                return _pod_status(exit_code=0)
            if call_count <= 2:
                return _jobs_response(completed=2, active=3)
            return _jobs_response(completed=5)

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.get_json = AsyncMock(side_effect=mock_get_json)
            kubectl.logs = AsyncMock(return_value="done")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "completed"
        assert result.total_task_count == 5
        assert result.task_completion_count == 5
        # Should have polled at least twice
        assert call_count >= 4

    @pytest.mark.asyncio
    async def test_requires_engine_pod(self) -> None:
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="")
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="engine pod not set"):
            await run_monitoring_phase(state, settings)

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """Monitoring times out if engine never terminates."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=1,
            monitor_timeout=0,  # immediate timeout
        )

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.get_json = AsyncMock(return_value=_pod_running())
            kubectl.logs = AsyncMock(return_value="")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "timeout"
