"""Phase 4: Deployment — full 10-step deploy sequence on K8s.

Replicates fast-test.sh programmatically using async subprocess wrappers.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from workflow_conductor.k8s import Helm, KindCluster, Kubectl, generate_helm_values
from workflow_conductor.models import PipelinePhase, PipelineState
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


async def run_deployment_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Execute the full K8s deployment sequence.

    Steps 0-9 mirror fast-test.sh:
    0. Ensure Kind cluster exists
    1. Create namespace
    2. Install hf-ops (infrastructure)
    3. Create ResourceQuota
    4. Stage data via hf-data
    5. Wait for data staging
    6. Clean up previous hf-run
    7. Create workflow.json ConfigMap
    8. Install hf-run
    9. Wait for engine pod readiness
    """
    display_phase_header(PipelinePhase.DEPLOYMENT)

    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    helm = Helm(kubeconfig=settings.kubernetes.kubeconfig)

    # Generate namespace
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    namespace = f"{settings.kubernetes.namespace_prefix}-{ts}"
    state.namespace = namespace

    charts_path = settings.hyperflow_k8s_deployment_path

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
        await cluster.use_context()
    state.cluster_ready = True

    # Step 1: Create namespace
    logger.info("Step 1: Creating namespace %s", namespace)
    await kubectl.create_namespace(namespace)

    # Step 2: Install hf-ops
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

    # Step 4: Stage data
    logger.info("Step 4: Staging data via hf-data")
    data_chart = str(Path(charts_path) / "charts" / "hyperflow-nfs-data")
    await helm.upgrade_install(
        "hf-data",
        data_chart,
        namespace=namespace,
        set_values={
            "workflow.image": settings.data_image,
        },
    )

    # Step 5: Wait for data staging
    logger.info("Step 5: Waiting for data staging job")
    await kubectl.wait_for_job(
        "hf-data",
        namespace=namespace,
        timeout=300,
    )

    # Step 6: Clean up previous hf-run
    logger.info("Step 6: Cleaning up previous hf-run")
    if await helm.release_exists("hf-run", namespace=namespace):
        await helm.uninstall("hf-run", namespace=namespace)
        await kubectl.wait_for_delete(
            "pod",
            namespace=namespace,
            label_selector="component=hyperflow-engine",
            timeout=60,
        )

    # Step 7: Inject workflow.json via ConfigMap
    logger.info("Step 7: Creating workflow.json ConfigMap")
    if state.workflow_json:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            json.dump(state.workflow_json, f)
            workflow_path = f.name
        state.workflow_json_path = workflow_path

        await kubectl.create_configmap_from_file(
            "workflow-json",
            workflow_path,
            namespace=namespace,
            file_key="workflow.json",
        )

    # Step 8: Install hf-run
    logger.info("Step 8: Installing hf-run")
    run_chart = str(Path(charts_path) / "charts" / "hyperflow-run")
    values = generate_helm_values(
        settings,
        state.workflow_plan,  # type: ignore[arg-type]
        namespace=namespace,
        resource_profiles=state.resource_profiles or None,
    )

    # Write generated values to temp file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        delete=False,
    ) as f:
        import yaml

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

    # Step 9: Wait for engine pod
    logger.info("Step 9: Waiting for engine pod readiness")
    pod_name = await kubectl.wait_for_pod(
        namespace=namespace,
        label_selector="component=hyperflow-engine",
        timeout=300,
    )
    state.engine_pod_name = pod_name
    logger.info("Engine pod ready: %s", pod_name)

    return state
