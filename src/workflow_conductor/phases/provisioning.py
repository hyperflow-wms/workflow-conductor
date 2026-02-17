"""Provisioning phase: infrastructure setup, data staging, and measurements.

Sets up Kind cluster, installs hf-ops and hf-run Helm charts, waits for
engine pod and nfs-data job, and queries infrastructure measurements.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from workflow_conductor.k8s import Helm, KindCluster, Kubectl
from workflow_conductor.k8s.values import generate_helm_values
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
    4. Install hf-run (engine waits for conductor signal)
    5. Wait for engine pod readiness
    6. Wait for nfs-data job (data container staging)
    7. Query cluster for infrastructure measurements
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
            await asyncio.gather(
                cluster.load_image(settings.hf_engine_image),
                cluster.load_image(settings.worker_image),
                cluster.load_image(settings.data_image),
            )

        # Always export Kind kubeconfig for Kind clusters — the system's
        # $KUBECONFIG env var may be too long or point to wrong clusters
        settings.kubernetes.kubeconfig = await cluster.export_kubeconfig()
    else:
        logger.info(
            "Using existing cluster (provider=%s)",
            settings.kubernetes.cluster_provider,
        )

    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    helm = Helm(kubeconfig=settings.kubernetes.kubeconfig)

    # Step 0.5: Clean up previous runs (old namespaces + cluster-scoped resources)
    logger.info("Cleaning up resources from previous runs")
    await kubectl.cleanup_previous_runs(settings.kubernetes.namespace_prefix)

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

    # Step 4: Install hf-run (engine waits for conductor signal)
    logger.info("Step 4: Installing hf-run")
    run_chart = str(Path(charts_path) / "charts" / "hyperflow-run")
    values = generate_helm_values(
        settings,
        state.workflow_plan,  # type: ignore[arg-type]
        namespace=namespace,
        resource_profiles=state.resource_profiles or None,
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(values, f)
        generated_values_path = f.name

    values_files = [generated_values_path]
    if settings.helm.run_values:
        values_files.insert(0, settings.helm.run_values)

    await helm.upgrade_install(
        "hf-run",
        run_chart,
        namespace=namespace,
        values_files=values_files,
        dependency_update=True,
        wait=True,
        timeout=settings.helm.timeout_run,
    )
    state.helm_release_name = "hf-run"

    # Step 5: Wait for engine pod
    logger.info("Step 5: Waiting for engine pod readiness")
    pod_name = await kubectl.wait_for_pod(
        namespace=namespace,
        label_selector="component=hyperflow-engine",
        timeout=300,
    )
    state.engine_pod_name = pod_name
    logger.info("Engine pod ready: %s", pod_name)

    # Step 6: Wait for nfs-data job (data container staging)
    logger.info("Step 6: Waiting for nfs-data job to complete")
    await kubectl.wait_for_job("nfs-data", namespace=namespace, timeout=300)

    # Step 7: Query infrastructure measurements
    logger.info("Step 7: Querying infrastructure measurements")
    node_info = await kubectl.get_nodes()
    state.infrastructure = InfrastructureMeasurements(
        namespace=namespace,
        node_count=node_info["node_count"],
        available_vcpus=node_info["total_cpu"],
        memory_gb=node_info["total_memory_gb"],
        k8s_version=node_info["k8s_version"],
    )

    return state
