"""Integration test fixtures — Kind cluster management."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from workflow_conductor.config import ConductorSettings
from workflow_conductor.k8s import KindCluster, Kubectl

CLUSTER_NAME = "hyperflow-test"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark all tests in integration/ as integration."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def settings() -> ConductorSettings:
    return ConductorSettings(
        kubernetes={"kind_config": "local/kind-config-3n.yaml"},  # type: ignore[arg-type]
    )


@pytest.fixture(scope="session")
def cluster_name() -> str:
    return CLUSTER_NAME


@pytest_asyncio.fixture(scope="session")
async def kind_cluster(
    request: pytest.FixtureRequest,
    cluster_name: str,
    settings: ConductorSettings,
) -> AsyncGenerator[KindCluster, None]:
    """Ensure a Kind cluster exists for integration tests.

    Reuses an existing cluster if present. Creates one if not.
    Deletes after tests unless --keep-cluster is passed.
    """
    cluster = KindCluster(
        name=cluster_name,
        config=settings.kubernetes.kind_config,
    )

    created = False
    if not await cluster.exists():
        await cluster.create()
        created = True
        # Load images
        for image in [
            settings.hf_engine_image,
            settings.worker_image,
            settings.data_image,
        ]:
            with contextlib.suppress(Exception):
                await cluster.load_image(image)

    await cluster.use_context()

    yield cluster

    keep = request.config.getoption("--keep-cluster", default=False)
    if created and not keep:
        await cluster.delete()


@pytest_asyncio.fixture
async def test_namespace(
    kind_cluster: KindCluster,
) -> AsyncGenerator[str, None]:
    """Create an isolated namespace for each test, clean up after."""
    kubectl = Kubectl()
    ts = datetime.now(UTC).strftime("%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    ns = f"test-{ts}-{suffix}"

    await kubectl.create_namespace(ns)
    yield ns
    await kubectl.delete_namespace(ns)
