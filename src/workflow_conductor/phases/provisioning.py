"""Provisioning phase: infrastructure setup, data staging, and measurements.

Extracts steps 0-5 from the original deployment phase and adds
infrastructure measurement queries (node count, vCPUs, memory).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from workflow_conductor.k8s import Helm, KindCluster, Kubectl
from workflow_conductor.models import (
    InfrastructureMeasurements,
    PipelinePhase,
    PipelineState,
)
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


async def run_provisioning_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Set up K8s infrastructure and measure the environment.

    Steps:
    0. Ensure Kind cluster exists (export kubeconfig)
    1. Create namespace with timestamp suffix
    2. Install hf-ops (NFS provisioner, RabbitMQ, KEDA)
    3. Create ResourceQuota
    4. Query cluster for infrastructure measurements

    Note: Data staging (nfs-data) is handled by the hf-run chart as a
    sub-chart dependency, not as a separate install. See deployment phase.
    """
    display_phase_header(PipelinePhase.PROVISIONING)

    # Step 0: Ensure Kind cluster
    if settings.kubernetes.cluster_provider == "kind":
        cluster = KindCluster(
            name=settings.kubernetes.cluster_name,
            config=settings.kubernetes.kind_config,
        )
        if not await cluster.exists():
            logger.info("Creating Kind cluster: %s", cluster.name)
            await cluster.create()
            for image in [
                settings.hf_engine_image,
                settings.worker_image,
                settings.data_image,
            ]:
                await cluster.load_image(image)

        # Always export Kind kubeconfig for Kind clusters — the system's
        # $KUBECONFIG env var may be too long or point to wrong clusters
        settings.kubernetes.kubeconfig = await cluster.export_kubeconfig()

    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    helm = Helm(kubeconfig=settings.kubernetes.kubeconfig)

    # Step 1: Create namespace
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    namespace = f"{settings.kubernetes.namespace_prefix}-{ts}"
    logger.info("Step 1: Creating namespace %s", namespace)
    await kubectl.create_namespace(namespace)
    state.namespace = namespace
    state.cluster_ready = True

    # Step 2: Install hf-ops
    charts_path = settings.hyperflow_k8s_deployment_path
    logger.info("Step 2: Installing hf-ops")
    ops_chart = str(Path(charts_path) / "charts" / "hyperflow-ops")
    ops_values = [settings.helm.ops_values] if settings.helm.ops_values else []
    await helm.upgrade_install(
        "hf-ops",
        ops_chart,
        namespace=namespace,
        values_files=ops_values,
        dependency_update=True,
        wait=True,
        timeout=settings.helm.timeout_ops,
    )

    # Step 3: Create ResourceQuota
    logger.info("Step 3: Creating ResourceQuota")
    await kubectl.create_resource_quota(
        "hflow-requests",
        namespace=namespace,
        hard_cpu=settings.resource_quota_cpu,
        hard_memory=settings.resource_quota_memory,
    )

    # Step 4: Query infrastructure measurements
    logger.info("Step 4: Querying infrastructure measurements")
    node_info = await kubectl.get_nodes()
    state.infrastructure = InfrastructureMeasurements(
        namespace=namespace,
        node_count=node_info["node_count"],
        available_vcpus=node_info["total_cpu"],
        memory_gb=node_info["total_memory_gb"],
        k8s_version=node_info["k8s_version"],
    )

    return state
