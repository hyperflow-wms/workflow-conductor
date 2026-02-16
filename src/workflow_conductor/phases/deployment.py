"""Phase 7: Deployment — deploy workflow on pre-provisioned K8s infrastructure.

After provisioning (steps 0-5) and generation (workflow.json), deployment:
- Cleans previous hf-run release
- Creates workflow.json ConfigMap
- Installs hf-run chart
- Waits for engine pod readiness
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from workflow_conductor.k8s import Helm, Kubectl, generate_helm_values
from workflow_conductor.models import PipelinePhase, PipelineState
from workflow_conductor.ui.display import display_phase_header

if TYPE_CHECKING:
    from workflow_conductor.config import ConductorSettings

logger = logging.getLogger(__name__)


async def run_deployment_phase(
    state: PipelineState,
    settings: ConductorSettings,
) -> PipelineState:
    """Deploy the workflow on pre-provisioned K8s infrastructure.

    Requires state.namespace and state.cluster_ready from provisioning.
    Requires state.workflow_json from generation.
    """
    display_phase_header(PipelinePhase.DEPLOYMENT)

    if not state.namespace:
        raise ValueError(
            "Cannot deploy: namespace not set (provisioning phase required)"
        )
    if not state.cluster_ready:
        raise ValueError(
            "Cannot deploy: cluster not ready (provisioning phase required)"
        )

    namespace = state.namespace
    kubectl = Kubectl(kubeconfig=settings.kubernetes.kubeconfig)
    helm = Helm(kubeconfig=settings.kubernetes.kubeconfig)
    charts_path = settings.hyperflow_k8s_deployment_path

    # Step 1: Clean up previous hf-run
    logger.info("Step 1: Cleaning up previous hf-run")
    if await helm.release_exists("hf-run", namespace=namespace):
        await helm.uninstall("hf-run", namespace=namespace)
        await kubectl.wait_for_delete(
            "pod",
            namespace=namespace,
            label_selector="component=hyperflow-engine",
            timeout=60,
        )

    # Step 2: Inject workflow.json via ConfigMap
    logger.info("Step 2: Creating workflow.json ConfigMap")
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

    # Step 3: Install hf-run
    logger.info("Step 3: Installing hf-run")
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

    # Step 4: Wait for engine pod
    logger.info("Step 4: Waiting for engine pod readiness")
    pod_name = await kubectl.wait_for_pod(
        namespace=namespace,
        label_selector="component=hyperflow-engine",
        timeout=300,
    )
    state.engine_pod_name = pod_name
    logger.info("Engine pod ready: %s", pod_name)

    return state
