"""Unit tests for Helm values generation."""

from __future__ import annotations

from workflow_conductor.config import ConductorSettings
from workflow_conductor.k8s.values import generate_helm_values
from workflow_conductor.models import ResourceProfile, WorkflowPlan


class TestGenerateHelmValues:
    def test_basic_values(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan(
            chromosomes=["22"],
            populations=["EUR"],
            parallelism=5,
        )
        values = generate_helm_values(settings, plan, namespace="test-ns")
        engine = values["hyperflow-engine"]
        assert engine["containers"]["hyperflow"]["image"] == settings.hf_engine_image
        # Conductor signal pattern: engine command waits for .conductor-ready
        assert "command" in engine["containers"]["hyperflow"]
        assert engine["containers"]["worker"]["image"] == settings.worker_image

    def test_volumes_complete(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        engine = values["hyperflow-engine"]
        mounts = engine["containers"]["hyperflow"]["volumeMounts"]
        assert any(m["name"] == "workflow-data" for m in mounts)
        assert any(m["name"] == "config-map" for m in mounts)
        # worker-config not mounted (only exists when workerPools enabled)
        assert not any(m["name"] == "worker-config" for m in mounts)
        vols = engine["volumes"]
        assert any(v["name"] == "workflow-data" for v in vols)
        assert any(v["name"] == "config-map" for v in vols)
        assert not any(v["name"] == "worker-config" for v in vols)

    def test_data_image(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        assert values["hyperflow-nfs-data"]["workflow"]["image"] == settings.data_image

    def test_with_resource_profiles(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        profiles = [
            ResourceProfile(
                task_type="individuals",
                cpu_request="500m",
                cpu_limit="1000m",
                memory_request="512Mi",
                memory_limit="1Gi",
            ),
        ]
        values = generate_helm_values(
            settings,
            plan,
            namespace="test-ns",
            resource_profiles=profiles,
        )
        resources = values["hyperflow-engine"]["jobTemplateResources"]
        assert "individuals" in resources
        assert resources["individuals"]["requests"]["cpu"] == "500m"

    def test_without_resource_profiles(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        assert "jobTemplateResources" not in values["hyperflow-engine"]

    def test_nfs_volume_capacity(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        assert values["nfs-volume"]["pv"]["capacity"]["storage"] == "10Gi"

    def test_nfs_volume_scales_for_large_data(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan(estimated_data_size_gb=12.0)
        values = generate_helm_values(settings, plan, namespace="test-ns")
        storage = values["nfs-volume"]["pv"]["capacity"]["storage"]
        # 12 * 2 + 5 = 29Gi
        assert storage == "29Gi"

    def test_nfs_volume_minimum_10gi(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan(estimated_data_size_gb=2.0)
        values = generate_helm_values(settings, plan, namespace="test-ns")
        assert values["nfs-volume"]["pv"]["capacity"]["storage"] == "10Gi"

    def test_worker_pools_disabled(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        assert values["workerPools"]["enabled"] is False

    def test_engine_command_waits_for_signal(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        engine = values["hyperflow-engine"]
        cmd = engine["containers"]["hyperflow"]["command"]
        # Command should wait for conductor signal
        cmd_str = cmd[2] if len(cmd) > 2 else ""
        assert ".conductor-ready" in cmd_str
        assert "hflow run workflow.json" in cmd_str

    def test_job_template_uses_hfmaster(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        engine = values["hyperflow-engine"]
        job_template = engine["configMap"]["data"]["job-template.yaml"]
        # Worker jobs use hfmaster (not hfworker) so single-node clusters work
        assert "hyperflow-wms/nodepool: hfmaster" in job_template
        assert "hfworker" not in job_template
