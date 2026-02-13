"""Unit tests for monitoring phase — mocked K8s operations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineState


class TestMonitoringPhase:
    @pytest.mark.asyncio
    async def test_monitoring_detects_completion(self) -> None:
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        jobs_response = {
            "items": [
                {
                    "metadata": {"name": "job-1"},
                    "status": {
                        "conditions": [
                            {
                                "type": "Complete",
                                "status": "True",
                            }
                        ]
                    },
                },
                {
                    "metadata": {"name": "job-2"},
                    "status": {
                        "conditions": [
                            {
                                "type": "Complete",
                                "status": "True",
                            }
                        ]
                    },
                },
            ]
        }

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.get_json = AsyncMock(return_value=jobs_response)
            kubectl.logs = AsyncMock(return_value="done")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "completed"
        assert result.total_task_count == 2
        assert result.task_completion_count == 2

    @pytest.mark.asyncio
    async def test_monitoring_detects_failure(self) -> None:
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace="test-ns",
            engine_pod_name="engine-pod-1",
        )
        settings = ConductorSettings(
            monitor_poll_interval=0,
            monitor_timeout=10,
        )

        jobs_response = {
            "items": [
                {
                    "metadata": {"name": "job-1"},
                    "status": {
                        "conditions": [
                            {
                                "type": "Failed",
                                "status": "True",
                            }
                        ]
                    },
                },
            ]
        }

        with patch("workflow_conductor.phases.monitoring.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.get_json = AsyncMock(return_value=jobs_response)
            kubectl.logs = AsyncMock(return_value="error")

            result = await run_monitoring_phase(state, settings)

        assert result.workflow_status == "failed"
