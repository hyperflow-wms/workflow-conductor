"""Unit tests for monitoring phase — mocked K8s operations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.k8s.kubectl import KubectlError
from workflow_conductor.models import PipelineState
from workflow_conductor.phases.monitoring import _count_jobs


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
    async def test_detects_workflow_completion(self) -> None:
        """Signal file with exit code 0 → workflow 'completed'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            # exec_in_pod returns "0\n" (exit code file content)
            kubectl.exec_in_pod = AsyncMock(return_value="0\n")
            kubectl.get_json = AsyncMock(return_value=_jobs_response(completed=5))
            kubectl.logs = AsyncMock(return_value="done")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "completed"
        assert result.total_task_count == 5
        assert result.task_completion_count == 5

    @pytest.mark.asyncio
    async def test_detects_workflow_failure(self) -> None:
        """Signal file with exit code 1 → workflow 'failed'."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.exec_in_pod = AsyncMock(return_value="1\n")
            kubectl.get_json = AsyncMock(
                return_value=_jobs_response(completed=2, failed=1)
            )
            kubectl.logs = AsyncMock(return_value="error")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "failed"

    @pytest.mark.asyncio
    async def test_waits_while_signal_file_missing(self) -> None:
        """Monitoring keeps polling while signal file doesn't exist yet."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        # First two exec_in_pod calls fail (file not found),
        # third returns exit code 0
        exec_calls = [
            KubectlError("cat failed: No such file"),
            KubectlError("cat failed: No such file"),
            "0\n",
        ]

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.exec_in_pod = AsyncMock(side_effect=exec_calls)
            kubectl.get_json = AsyncMock(
                side_effect=[
                    _jobs_response(completed=2, active=3),
                    _jobs_response(completed=4, active=1),
                    _jobs_response(completed=5),
                ]
            )
            kubectl.logs = AsyncMock(return_value="done")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "completed"
        assert result.total_task_count == 5
        assert result.task_completion_count == 5
        assert kubectl.exec_in_pod.call_count == 3

    @pytest.mark.asyncio
    async def test_requires_engine_pod(self) -> None:
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(namespace="test-ns", engine_pod_name="")
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="engine pod not set"):
            await run_monitoring_phase(state, settings)

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """Monitoring times out if signal file never appears."""
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
            kubectl.exec_in_pod = AsyncMock(side_effect=KubectlError("file not found"))
            kubectl.get_json = AsyncMock(return_value=_jobs_response())
            kubectl.logs = AsyncMock(return_value="")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "timeout"
