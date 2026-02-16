"""Unit tests for the provisioning phase."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelinePhase, PipelineState, WorkflowPlan


@pytest.fixture
def settings() -> ConductorSettings:
    return ConductorSettings()


@pytest.fixture
def state_after_validation() -> PipelineState:
    """State as it would be after validation (Gate 1) approves."""
    return PipelineState(
        user_prompt="Analyze EUR chr22",
        current_phase=PipelinePhase.PROVISIONING,
        user_approved_plan=True,
        workflow_plan=WorkflowPlan(
            chromosomes=["22"],
            populations=["EUR"],
            parallelism=10,
            estimated_data_size_gb=0.5,
            description="EUR chr22 analysis",
        ),
    )


def _mock_kubectl() -> MagicMock:
    kubectl = MagicMock()
    kubectl.create_namespace = AsyncMock(return_value="namespace/wf-test created")
    kubectl.create_resource_quota = AsyncMock(return_value="")
    kubectl.get_nodes = AsyncMock(
        return_value={
            "node_count": 2,
            "total_cpu": 8,
            "total_memory_gb": 16.0,
            "k8s_version": "v1.31.0",
        }
    )
    return kubectl


def _mock_helm() -> MagicMock:
    helm = MagicMock()
    helm.upgrade_install = AsyncMock()
    return helm


def _mock_kind_cluster(exists: bool = True) -> MagicMock:
    cluster = MagicMock()
    cluster.exists = AsyncMock(return_value=exists)
    cluster.create = AsyncMock()
    cluster.load_image = AsyncMock()
    cluster.use_context = AsyncMock()
    cluster.export_kubeconfig = AsyncMock(return_value="/tmp/mock-kubeconfig.yaml")
    cluster.name = "hf-conductor"
    return cluster


class TestProvisioningPhase:
    @pytest.mark.asyncio
    async def test_sets_namespace(
        self, state_after_validation: PipelineState, settings: ConductorSettings
    ) -> None:
        with (
            patch(
                "workflow_conductor.phases.provisioning.Kubectl",
                return_value=_mock_kubectl(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.Helm",
                return_value=_mock_helm(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.KindCluster",
                return_value=_mock_kind_cluster(),
            ),
        ):
            from workflow_conductor.phases.provisioning import (
                run_provisioning_phase,
            )

            result = await run_provisioning_phase(state_after_validation, settings)
        assert result.namespace != ""
        assert result.namespace.startswith(settings.kubernetes.namespace_prefix)

    @pytest.mark.asyncio
    async def test_sets_cluster_ready(
        self, state_after_validation: PipelineState, settings: ConductorSettings
    ) -> None:
        with (
            patch(
                "workflow_conductor.phases.provisioning.Kubectl",
                return_value=_mock_kubectl(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.Helm",
                return_value=_mock_helm(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.KindCluster",
                return_value=_mock_kind_cluster(),
            ),
        ):
            from workflow_conductor.phases.provisioning import (
                run_provisioning_phase,
            )

            result = await run_provisioning_phase(state_after_validation, settings)
        assert result.cluster_ready is True

    @pytest.mark.asyncio
    async def test_populates_infrastructure(
        self, state_after_validation: PipelineState, settings: ConductorSettings
    ) -> None:
        with (
            patch(
                "workflow_conductor.phases.provisioning.Kubectl",
                return_value=_mock_kubectl(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.Helm",
                return_value=_mock_helm(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.KindCluster",
                return_value=_mock_kind_cluster(),
            ),
        ):
            from workflow_conductor.phases.provisioning import (
                run_provisioning_phase,
            )

            result = await run_provisioning_phase(state_after_validation, settings)
        assert result.infrastructure is not None
        assert result.infrastructure.node_count == 2
        assert result.infrastructure.available_vcpus == 8
        assert result.infrastructure.memory_gb == 16.0
        assert result.infrastructure.k8s_version == "v1.31.0"
        assert result.infrastructure.namespace == result.namespace

    @pytest.mark.asyncio
    async def test_creates_kind_cluster_when_missing(
        self, state_after_validation: PipelineState, settings: ConductorSettings
    ) -> None:
        kind_cluster = _mock_kind_cluster(exists=False)
        with (
            patch(
                "workflow_conductor.phases.provisioning.Kubectl",
                return_value=_mock_kubectl(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.Helm",
                return_value=_mock_helm(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.KindCluster",
                return_value=kind_cluster,
            ),
        ):
            from workflow_conductor.phases.provisioning import (
                run_provisioning_phase,
            )

            await run_provisioning_phase(state_after_validation, settings)
        kind_cluster.create.assert_awaited_once()
        assert kind_cluster.load_image.await_count == 3

    @pytest.mark.asyncio
    async def test_installs_hf_ops(
        self, state_after_validation: PipelineState, settings: ConductorSettings
    ) -> None:
        helm = _mock_helm()
        with (
            patch(
                "workflow_conductor.phases.provisioning.Kubectl",
                return_value=_mock_kubectl(),
            ),
            patch(
                "workflow_conductor.phases.provisioning.Helm",
                return_value=helm,
            ),
            patch(
                "workflow_conductor.phases.provisioning.KindCluster",
                return_value=_mock_kind_cluster(),
            ),
        ):
            from workflow_conductor.phases.provisioning import (
                run_provisioning_phase,
            )

            await run_provisioning_phase(state_after_validation, settings)
        # Only hf-ops is installed in provisioning (data staging is part of hf-run)
        assert helm.upgrade_install.await_count == 1
