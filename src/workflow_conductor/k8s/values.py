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
            "echo $? > /work_dir/.workflow-exit-code ; "
            'if [ "$(ls -A /work_dir/logs-hf)" ]; then '
            "  echo 1 > /work_dir/postprocStart ; "
            "else "
            "  echo 'HyperFlow logs not collected.' ; "
            "fi ; "
            "echo 'Workflow finished.' ; "
            "while true; do sleep 5 ; done"
        ),
    ]

    # Job template for HyperFlow worker pods. Overrides the chart default
    # to use hfmaster nodeSelector (same as engine/NFS/redis) so that
    # single-node Kind clusters work without a separate hfworker node.
    job_template = """\
apiVersion: batch/v1
kind: Job
metadata:
  name: job${jobName}
spec:
  ttlSecondsAfterFinished: 100
  template:
    metadata:
      labels:
        app: hyperflow
    spec:
      restartPolicy: Never
      containers:
      - name: test
        image: ${containerName}
        imagePullPolicy: IfNotPresent
        env:
          - name: HF_VAR_WORK_DIR
            value: "${workingDirPath}"
          - name: HF_VAR_WAIT_FOR_INPUT_FILES
            value: "0"
          - name: HF_VAR_NUM_RETRIES
            value: "1"
          - name: HF_VAR_ENABLE_TRACING
            value: "${enableTracing}"
          - name: HF_VAR_ENABLE_OTEL
            value: "${enableOtel}"
          - name: HF_VAR_OT_PARENT_ID
            value: "${optParentId}"
          - name: HF_VAR_OT_TRACE_ID
            value: "${optTraceId}"
          - name: HF_LOG_NODE_NAME
            valueFrom:
              fieldRef:
                fieldPath: spec.nodeName
          - name: HF_LOG_POD_NAME
            valueFrom:
              fieldRef:
                fieldPath: metadata.name
          - name: HF_LOG_POD_NAMESPACE
            valueFrom:
              fieldRef:
                fieldPath: metadata.namespace
          - name: HF_LOG_POD_IP
            valueFrom:
              fieldRef:
                fieldPath: status.podIP
          - name: HF_LOG_POD_SERVICE_ACCOUNT
            valueFrom:
              fieldRef:
                fieldPath: spec.serviceAccountName
          - name: HF_VAR_FS_MONIT_ENABLED
            value: "0"
        command:
          - "/bin/sh"
          - "-c"
          - >
            ${command}; exitCode=$? ;
            if [ $exitCode -ne 0 ]; then
            echo "${command} failed (exit $exitCode)" ;
            exit 1 ; fi ;
        workingDir: ${workingDirPath}
        resources:
          requests:
            cpu: ${cpuRequest}
            memory: ${memRequest}
        volumeMounts:
        - name: my-pvc-nfs
          mountPath: ${volumePath}
      nodeSelector:
        hyperflow-wms/nodepool: hfmaster
      volumes:
      - name: my-pvc-nfs
        persistentVolumeClaim:
          claimName: nfs"""

    values: dict[str, Any] = {
        "hyperflow-engine": {
            "containers": {
                "hyperflow": {
                    "image": settings.hf_engine_image,
                    "command": engine_command,
                    "volumeMounts": [
                        {"name": "workflow-data", "mountPath": "/work_dir"},
                        {
                            "name": "config-map",
                            "mountPath": "/opt/hyperflow/job-template.yaml",
                            "subPath": "job-template.yaml",
                            "readOnly": True,
                        },
                        # worker-config mount removed: ConfigMap only exists
                        # when workerPools.enabled=true (workerpools-cm.yml)
                    ],
                },
                "worker": {
                    "image": settings.worker_image,
                },
            },
            "volumes": [
                {
                    "name": "config-map",
                    "configMap": {"name": "hyperflow-config"},
                },
                {
                    "name": "workflow-data",
                    "persistentVolumeClaim": {"claimName": "nfs"},
                },
                # worker-config volume removed: ConfigMap only exists
                # when workerPools.enabled=true (workerpools-cm.yml)
            ],
            "configMap": {
                "data": {
                    "job-template.yaml": job_template,
                },
            },
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
