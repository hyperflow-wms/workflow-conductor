"""E2E test fixtures — full pipeline infrastructure."""

from __future__ import annotations

import os
import shutil

import pytest
from dotenv import load_dotenv

from workflow_conductor.config import ConductorSettings

# Load .env into os.environ so guard fixtures can see API keys
load_dotenv()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark all tests in e2e/ as e2e."""
    for item in items:
        if "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session", autouse=True)
def check_docker() -> None:
    """Skip all E2E tests if Docker is not available."""
    if not shutil.which("docker"):
        pytest.skip("Docker not found on PATH")
    ret = os.system("docker info > /dev/null 2>&1")  # noqa: S605
    if ret != 0:
        pytest.skip("Docker daemon not running")


@pytest.fixture(scope="session", autouse=True)
def check_llm_key() -> None:
    """Skip all E2E tests if no LLM API key is configured."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        pytest.skip("No LLM API key (ANTHROPIC_API_KEY or GOOGLE_API_KEY)")


@pytest.fixture(scope="session")
def e2e_settings() -> ConductorSettings:
    """Settings for E2E tests with auto-approve and auto-teardown."""
    k8s_path = os.environ.get(
        "HF_CONDUCTOR_HYPERFLOW_K8S_DEPLOYMENT_PATH",
        "../../hyperflow-k8s-deployment",
    )
    return ConductorSettings(
        auto_approve=True,
        auto_teardown=True,
        monitor_poll_interval=5,
        monitor_timeout=600,
        hyperflow_k8s_deployment_path=k8s_path,
        kubernetes={"kind_config": "local/kind-config-3n.yaml"},  # type: ignore[arg-type]
    )
