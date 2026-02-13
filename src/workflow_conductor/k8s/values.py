"""Generate Helm values for HyperFlow deployments."""

from __future__ import annotations

from typing import Any

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import ResourceProfile, WorkflowPlan


def generate_helm_values(
    settings: ConductorSettings,
    plan: WorkflowPlan,
    *,
    namespace: str,
    resource_profiles: list[ResourceProfile] | None = None,
) -> dict[str, Any]:
    """Generate Helm values dict for the hyperflow-run chart.

    Includes image overrides, workflow ConfigMap mount, and optional
    resource profiles from the Workflow Profiler.
    """
    values: dict[str, Any] = {
        "hyperflow-engine": {
            "containers": {
                "hyperflow": {
                    "image": settings.hf_engine_image,
                    "autoRun": True,
                },
                "worker": {
                    "image": settings.worker_image,
                },
            },
            "volumeMounts": [
                {
                    "name": "workflow-json",
                    "mountPath": "/work_dir/workflow.json",
                    "subPath": "workflow.json",
                    "readOnly": True,
                },
            ],
            "volumes": [
                {
                    "name": "workflow-json",
                    "configMap": {
                        "name": "workflow-json",
                    },
                },
            ],
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
