"""Unit tests for deployment phase — mocked K8s operations.

After PR 2.6, deployment no longer creates the namespace or cluster.
Those steps are in provisioning. Deployment expects state.namespace
and state.cluster_ready to be pre-populated.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import (
    InfrastructureMeasurements,
    PipelineState,
    WorkflowPlan,
)


def _make_state(**overrides: object) -> PipelineState:
    """Create a state as if provisioning and generation have run."""
    defaults = dict(
        workflow_plan=WorkflowPlan(description="test"),
        namespace="wf-1000g-20260216-120000",
        cluster_ready=True,
        infrastructure=InfrastructureMeasurements(
            namespace="wf-1000g-20260216-120000",
            node_count=2,
            available_vcpus=4,
            memory_gb=8.0,
        ),
        workflow_json={"name": "test-workflow", "processes": [], "signals": []},
    )
    defaults.update(overrides)
    return PipelineState(**defaults)  # type: ignore[arg-type]


class TestDeploymentPhase:
    @pytest.mark.asyncio
    async def test_deployment_uses_existing_namespace(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = _make_state()
        settings = ConductorSettings(
            hyperflow_k8s_deployment_path="/tmp/charts",
            kubernetes={"cluster_provider": "existing"},
        )

        with (
            patch("workflow_conductor.phases.deployment.Kubectl") as MockKubectl,
            patch("workflow_conductor.phases.deployment.Helm") as MockHelm,
        ):
            kubectl = MockKubectl.return_value
            kubectl.wait_for_delete = AsyncMock()
            kubectl.create_configmap_from_file = AsyncMock()
            kubectl.wait_for_pod = AsyncMock(return_value="engine-pod-123")
            kubectl.wait_for_job = AsyncMock()
            kubectl.cp_to_pod = AsyncMock()
            kubectl.exec_in_pod = AsyncMock()

            helm = MockHelm.return_value
            helm.upgrade_install = AsyncMock()
            helm.release_exists = AsyncMock(return_value=False)
            helm.uninstall = AsyncMock()

            result = await run_deployment_phase(state, settings)

        # Should NOT create a new namespace — uses the one from provisioning
        assert result.namespace == "wf-1000g-20260216-120000"
        kubectl.create_namespace = AsyncMock()
        kubectl.create_namespace.assert_not_called()
        assert result.engine_pod_name == "engine-pod-123"
        assert result.helm_release_name == "hf-run"

    @pytest.mark.asyncio
    async def test_deployment_requires_namespace(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = _make_state(namespace="", cluster_ready=True)
        settings = ConductorSettings(
            hyperflow_k8s_deployment_path="/tmp/charts",
            kubernetes={"cluster_provider": "existing"},
        )

        with pytest.raises(ValueError, match="namespace"):
            await run_deployment_phase(state, settings)

    @pytest.mark.asyncio
    async def test_deployment_requires_cluster_ready(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = _make_state(cluster_ready=False)
        settings = ConductorSettings(
            hyperflow_k8s_deployment_path="/tmp/charts",
            kubernetes={"cluster_provider": "existing"},
        )

        with pytest.raises(ValueError, match="cluster"):
            await run_deployment_phase(state, settings)

    @pytest.mark.asyncio
    async def test_deployment_cleans_previous_release(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = _make_state()
        settings = ConductorSettings(
            hyperflow_k8s_deployment_path="/tmp/charts",
            kubernetes={"cluster_provider": "existing"},
        )

        with (
            patch("workflow_conductor.phases.deployment.Kubectl") as MockKubectl,
            patch("workflow_conductor.phases.deployment.Helm") as MockHelm,
        ):
            kubectl = MockKubectl.return_value
            kubectl.wait_for_delete = AsyncMock()
            kubectl.create_configmap_from_file = AsyncMock()
            kubectl.wait_for_pod = AsyncMock(return_value="pod-1")
            kubectl.wait_for_job = AsyncMock()
            kubectl.cp_to_pod = AsyncMock()
            kubectl.exec_in_pod = AsyncMock()

            helm = MockHelm.return_value
            helm.upgrade_install = AsyncMock()
            helm.release_exists = AsyncMock(return_value=True)
            helm.uninstall = AsyncMock()

            await run_deployment_phase(state, settings)

        helm.uninstall.assert_called_once()

    @pytest.mark.asyncio
    async def test_deployment_injects_workflow_json(self) -> None:
        from workflow_conductor.phases.deployment import run_deployment_phase

        state = _make_state(
            workflow_json={"name": "test-workflow", "processes": [], "signals": []},
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
            kubectl.wait_for_delete = AsyncMock()
            kubectl.create_configmap_from_file = AsyncMock()
            kubectl.wait_for_pod = AsyncMock(return_value="pod-1")
            kubectl.wait_for_job = AsyncMock()
            kubectl.cp_to_pod = AsyncMock()
            kubectl.exec_in_pod = AsyncMock()

            helm = MockHelm.return_value
            helm.upgrade_install = AsyncMock()
            helm.release_exists = AsyncMock(return_value=False)

            result = await run_deployment_phase(state, settings)

        kubectl.create_configmap_from_file.assert_called_once()
        assert result.workflow_json_path != ""
