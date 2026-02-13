"""E2E test fixtures — full pipeline infrastructure."""

from __future__ import annotations

import pytest

from workflow_conductor.config import ConductorSettings


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark all tests in e2e/ as e2e."""
    for item in items:
        if "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def e2e_settings() -> ConductorSettings:
    """Settings for E2E tests with auto-approve and auto-teardown."""
    return ConductorSettings(
        auto_approve=True,
        auto_teardown=True,
        monitor_poll_interval=5,
        monitor_timeout=600,
    )
