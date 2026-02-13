"""Shared test fixtures with CLI options."""

from __future__ import annotations

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import WorkflowPlan


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--keep-cluster", action="store_true", default=False)
    parser.addoption("--skip-k8s", action="store_true", default=False)


@pytest.fixture
def settings() -> ConductorSettings:
    return ConductorSettings(
        kubernetes={"cluster_name": "test-cluster"},  # type: ignore[arg-type]
        helm={"charts_path": "/tmp/charts"},  # type: ignore[arg-type]
        auto_approve=True,
        auto_teardown=True,
    )


@pytest.fixture
def sample_plan() -> WorkflowPlan:
    return WorkflowPlan(
        chromosomes=["1", "2", "3", "4", "5"],
        populations=["EUR", "EAS"],
        parallelism=10,
        estimated_data_size_gb=5.3,
        description="Analyze genetic variants across EUR and EAS for chr1-5.",
    )
