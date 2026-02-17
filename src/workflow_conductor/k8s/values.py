"""Generate Helm values for HyperFlow deployments."""

from __future__ import annotations

from typing import Any

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import ResourceProfile, WorkflowPlan


def _nfs_storage_size(plan: WorkflowPlan) -> str:
    """Compute NFS PVC size based on estimated data size.

    Default 10Gi is sufficient for chr1-10 from the data container.
    For remote chromosomes (larger data), scale up based on the Composer's
    estimated_data_size_gb (accounts for compressed + uncompressed).
    """
    if plan.estimated_data_size_gb > 5:
        size_gi = max(10, int(plan.estimated_data_size_gb * 2) + 5)
        return f"{size_gi}Gi"
    return "10Gi"


def generate_helm_values(
    settings: ConductorSettings,
    plan: WorkflowPlan,
    *,
    namespace: str,
    resource_profiles: list[ResourceProfile] | None = None,
) -> dict[str, Any]:
    """Generate Helm values dict for the hyperflow-run chart.

    Includes image overrides and optional resource profiles from the
    Workflow Profiler.

    The volumes/volumeMounts lists must be COMPLETE because Helm replaces
    (not merges) list values.  We include the chart defaults here.

    Workflow.json is delivered via kubectl cp (conductor signal pattern),
    not via ConfigMap mount.
    """
    # Custom engine command: wait for conductor to signal readiness
    # before running hflow. This prevents the engine from running the
    # default workflow.json shipped by the data image before the
    # conductor can overwrite it with the generated one.
    engine_command = [
        "/bin/sh",
        "-c",
        (
            "echo 'Waiting for conductor signal...' ; "
            "while ! [ -f /work_dir/.conductor-ready ]; do sleep 2 ; done ; "
            "echo 'Conductor signal received.' ; "
            "cd /work_dir ; "
            "mkdir -p /work_dir/logs-hf ; "
            "echo 'Running workflow:' ; "
            "hflow run workflow.json ; "
            'if [ "$(ls -A /work_dir/logs-hf)" ]; then '
            "  echo 1 > /work_dir/postprocStart ; "
            "else "
            "  echo 'HyperFlow logs not collected.' ; "
            "fi ; "
            "echo 'Workflow finished.' ; "
            "while true; do sleep 5 ; done"
        ),
    ]

    values: dict[str, Any] = {
        "hyperflow-engine": {
            "containers": {
                "hyperflow": {
                    "image": settings.hf_engine_image,
                    "command": engine_command,
                    "volumeMounts": [
                        # Chart defaults
                        {"name": "workflow-data", "mountPath": "/work_dir"},
                        {
                            "name": "config-map",
                            "mountPath": "/opt/hyperflow/job-template.yaml",
                            "subPath": "job-template.yaml",
                            "readOnly": True,
                        },
                        {
                            "name": "worker-config",
                            "mountPath": (
                                "/work_dir/workflow.config.executionModels.json"
                            ),
                            "subPath": "workflow.config.executionModels.json",
                            "readOnly": True,
                        },
                    ],
                },
                "worker": {
                    "image": settings.worker_image,
                },
            },
            # Complete volumes list (chart defaults only)
            "volumes": [
                {
                    "name": "config-map",
                    "configMap": {"name": "hyperflow-config"},
                },
                {
                    "name": "workflow-data",
                    "persistentVolumeClaim": {"claimName": "nfs"},
                },
                {
                    "name": "worker-config",
                    "configMap": {"name": "worker-config"},
                },
            ],
        },
        "nfs-volume": {
            "pv": {
                "capacity": {
                    "storage": _nfs_storage_size(plan),
                },
            },
        },
        # Disable worker pools (1000genome uses job-based execution, not
        # the WorkerPool CRD which requires the operator chart in hf-ops)
        "workerPools": {
            "enabled": False,
        },
        "hyperflow-nfs-data": {
            "workflow": {
                "image": settings.data_image,
            },
        },
    }

    if resource_profiles:
        job_template_resources: dict[str, Any] = {}
        for profile in resource_profiles:
            job_template_resources[profile.task_type] = {
                "requests": {
                    "cpu": profile.cpu_request,
                    "memory": profile.memory_request,
                },
                "limits": {
                    "cpu": profile.cpu_limit,
                    "memory": profile.memory_limit,
                },
            }
        values["hyperflow-engine"]["jobTemplateResources"] = job_template_resources

    return values
