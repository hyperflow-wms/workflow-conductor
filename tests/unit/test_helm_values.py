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
        assert "command" in engine["containers"]["hyperflow"]
        assert engine["containers"]["worker"]["image"] == settings.worker_image

    def test_configmap_volume_mount(self) -> None:
        settings = ConductorSettings()
        plan = WorkflowPlan()
        values = generate_helm_values(settings, plan, namespace="test-ns")
        engine = values["hyperflow-engine"]
        mounts = engine["containers"]["hyperflow"]["volumeMounts"]
        assert any(m["name"] == "workflow-json" for m in mounts)
        assert any(m["mountPath"] == "/work_dir/workflow.json" for m in mounts)
        # Chart defaults must be preserved
        assert any(m["name"] == "workflow-data" for m in mounts)
        assert any(m["name"] == "config-map" for m in mounts)
        vols = engine["volumes"]
        assert any(v["name"] == "workflow-json" for v in vols)
        assert any(v["name"] == "workflow-data" for v in vols)

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
