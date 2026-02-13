"""Integration tests for K8s deployment on Kind cluster.

These tests require a running Kind cluster with loaded images.
Run with: make test-integration
"""

from __future__ import annotations

import pytest

from workflow_conductor.k8s import Helm, KindCluster, Kubectl


@pytest.mark.integration
class TestClusterExists:
    @pytest.mark.asyncio
    async def test_cluster_is_running(self, kind_cluster: KindCluster) -> None:
        assert await kind_cluster.exists()

    @pytest.mark.asyncio
    async def test_kubectl_context_is_set(self, kind_cluster: KindCluster) -> None:
        kubectl = Kubectl()
        result = await kubectl.get_json("nodes")
        nodes = result.get("items", [])
        assert len(nodes) >= 1


@pytest.mark.integration
class TestNamespaceCreation:
    @pytest.mark.asyncio
    async def test_create_and_delete_namespace(self, kind_cluster: KindCluster) -> None:
        kubectl = Kubectl()
        ns = "test-ns-lifecycle"

        await kubectl.create_namespace(ns)
        namespaces = await kubectl.get_json("namespaces")
        ns_names = [item["metadata"]["name"] for item in namespaces.get("items", [])]
        assert ns in ns_names

        await kubectl.delete_namespace(ns, wait=True)


@pytest.mark.integration
class TestHelmOperations:
    @pytest.mark.asyncio
    async def test_helm_list_empty(
        self,
        kind_cluster: KindCluster,
        test_namespace: str,
    ) -> None:
        helm = Helm()
        releases = await helm.list_releases(namespace=test_namespace)
        assert releases == []
