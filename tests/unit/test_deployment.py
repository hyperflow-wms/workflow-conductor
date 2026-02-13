"""Unit tests for deployment phase — mocked K8s operations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineState, WorkflowPlan


class TestDeploymentPhase:
    @pytest.mark.asyncio
    async def test_deployment_sets_namespace(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = PipelineState(
            workflow_plan=WorkflowPlan(description="test"),
        )
        settings = ConductorSettings(
            hyperflow_k8s_deployment_path="/tmp/charts",
            kubernetes={"cluster_provider": "existing"},
        )

        with (
            patch("workflow_conductor.phases.deployment.Kubectl") as MockKubectl,
            patch("workflow_conductor.phases.deployment.Helm") as MockHelm,
        ):
            kubectl = MockKubectl.return_value
            kubectl.create_namespace = AsyncMock()
            kubectl.create_resource_quota = AsyncMock()
            kubectl.wait_for_job = AsyncMock()
            kubectl.wait_for_delete = AsyncMock()
            kubectl.create_configmap_from_file = AsyncMock()
            kubectl.wait_for_pod = AsyncMock(return_value="engine-pod-123")

            helm = MockHelm.return_value
            helm.upgrade_install = AsyncMock()
            helm.release_exists = AsyncMock(return_value=False)
            helm.uninstall = AsyncMock()

            result = await run_deployment_phase(state, settings)

        assert result.namespace.startswith("wf-1000g-")
        assert result.cluster_ready is True
        assert result.engine_pod_name == "engine-pod-123"
        assert result.helm_release_name == "hf-run"

    @pytest.mark.asyncio
    async def test_deployment_cleans_previous_release(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = PipelineState(
            workflow_plan=WorkflowPlan(description="test"),
        )
        settings = ConductorSettings(
            hyperflow_k8s_deployment_path="/tmp/charts",
            kubernetes={"cluster_provider": "existing"},
        )

        with (
            patch("workflow_conductor.phases.deployment.Kubectl") as MockKubectl,
            patch("workflow_conductor.phases.deployment.Helm") as MockHelm,
        ):
            kubectl = MockKubectl.return_value
            kubectl.create_namespace = AsyncMock()
            kubectl.create_resource_quota = AsyncMock()
            kubectl.wait_for_job = AsyncMock()
            kubectl.wait_for_delete = AsyncMock()
            kubectl.create_configmap_from_file = AsyncMock()
            kubectl.wait_for_pod = AsyncMock(return_value="pod-1")

            helm = MockHelm.return_value
            helm.upgrade_install = AsyncMock()
            helm.release_exists = AsyncMock(return_value=True)
            helm.uninstall = AsyncMock()

            await run_deployment_phase(state, settings)

        helm.uninstall.assert_called_once()

    @pytest.mark.asyncio
    async def test_deployment_injects_workflow_json(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = PipelineState(
            workflow_plan=WorkflowPlan(description="test"),
            workflow_json={"name": "test-workflow"},
        )
        settings = ConductorSettings(
            hyperflow_k8s_deployment_path="/tmp/charts",
            kubernetes={"cluster_provider": "existing"},
        )

        with (
            patch("workflow_conductor.phases.deployment.Kubectl") as MockKubectl,
            patch("workflow_conductor.phases.deployment.Helm") as MockHelm,
        ):
            kubectl = MockKubectl.return_value
            kubectl.create_namespace = AsyncMock()
            kubectl.create_resource_quota = AsyncMock()
            kubectl.wait_for_job = AsyncMock()
            kubectl.wait_for_delete = AsyncMock()
            kubectl.create_configmap_from_file = AsyncMock()
            kubectl.wait_for_pod = AsyncMock(return_value="pod-1")

            helm = MockHelm.return_value
            helm.upgrade_install = AsyncMock()
            helm.release_exists = AsyncMock(return_value=False)

            result = await run_deployment_phase(state, settings)

        kubectl.create_configmap_from_file.assert_called_once()
        assert result.workflow_json_path != ""
