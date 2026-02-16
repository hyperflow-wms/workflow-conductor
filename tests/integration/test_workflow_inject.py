"""Integration tests for ConfigMap workflow.json injection.

Uses kr8s for async K8s assertions. Requires a running Kind cluster.
Run with: make test-integration
"""

from __future__ import annotations

import json
import tempfile

import kr8s
import pytest

from workflow_conductor.k8s import KindCluster, Kubectl


@pytest.mark.integration
class TestConfigMapInjection:
    @pytest.mark.asyncio
    async def test_create_configmap_from_file(
        self,
        kind_cluster: KindCluster,
        test_namespace: str,
    ) -> None:
        """Verify ConfigMap creation and content via kr8s."""
        kubectl = Kubectl()

        workflow = {"name": "test-workflow", "tasks": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(workflow, f)
            workflow_path = f.name

        await kubectl.create_configmap_from_file(
            "workflow-json",
            workflow_path,
            namespace=test_namespace,
            file_key="workflow.json",
        )

        # Verify with kr8s
        api = await kr8s.asyncio.api()
        configmaps = [
            cm
            async for cm in api.get(
                "configmaps",
                namespace=test_namespace,
                label_selector="",
            )
        ]

        cm_names = [cm.name for cm in configmaps]
        assert "workflow-json" in cm_names

        # Verify content
        for cm in configmaps:
            if cm.name == "workflow-json":
                data = cm.raw.get("data", {})
                assert "workflow.json" in data
                parsed = json.loads(data["workflow.json"])
                assert parsed["name"] == "test-workflow"

    @pytest.mark.asyncio
    async def test_configmap_idempotent_update(
        self,
        kind_cluster: KindCluster,
        test_namespace: str,
    ) -> None:
        """Verify ConfigMap update is idempotent (dry-run + apply)."""
        kubectl = Kubectl()

        # Create initial
        wf1 = {"name": "v1", "tasks": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(wf1, f)
            path1 = f.name

        await kubectl.create_configmap_from_file(
            "workflow-json",
            path1,
            namespace=test_namespace,
            file_key="workflow.json",
        )

        # Update with new content
        wf2 = {"name": "v2", "tasks": ["task1"]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(wf2, f)
            path2 = f.name

        await kubectl.create_configmap_from_file(
            "workflow-json",
            path2,
            namespace=test_namespace,
            file_key="workflow.json",
        )

        # Verify updated content with kr8s
        api = await kr8s.asyncio.api()
        configmaps = [
            cm
            async for cm in api.get(
                "configmaps",
                namespace=test_namespace,
            )
        ]

        for cm in configmaps:
            if cm.name == "workflow-json":
                data = cm.raw.get("data", {})
                parsed = json.loads(data["workflow.json"])
                assert parsed["name"] == "v2"
                assert parsed["tasks"] == ["task1"]
