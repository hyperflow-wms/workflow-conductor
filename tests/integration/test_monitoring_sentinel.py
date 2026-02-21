"""Integration tests for Execution Sentinel wired into Phase 9.

Each test gets an isolated namespace (test_namespace fixture) and a
"fake engine pod" — a busybox container named "hyperflow" that either
writes the signal file after a short delay (happy/failure paths) or
never writes it (timeout path).

No HyperFlow images are required.  busybox:1.36 is pulled from Docker Hub
by the Kind node on first use.

Run with:  make test-integration
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from execution_sentinel.config import SentinelSettings
from execution_sentinel.models import MonitoringContext, TaskSpec
from execution_sentinel.sentinel import Sentinel
from workflow_conductor.config import ConductorSettings
from workflow_conductor.k8s import Kubectl
from workflow_conductor.models import PipelineState

# ---------------------------------------------------------------------------
# Pod manifest helpers
# ---------------------------------------------------------------------------

_SENTINEL_TEST_LABEL = "hf-sentinel-test"


def _engine_pod_manifest(
    name: str,
    namespace: str,
    *,
    delay_seconds: int = 3,
    exit_code: int = 0,
) -> dict:  # type: ignore[type-arg]
    """Busybox pod with a 'hyperflow' container that writes the signal file."""
    cmd = (
        f"mkdir -p /work_dir && "
        f"sleep {delay_seconds} && "
        f"echo {exit_code} > /work_dir/.workflow-exit-code && "
        f"sleep 3600"
    )
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": _SENTINEL_TEST_LABEL},
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "hyperflow",
                    "image": "busybox:1.36",
                    "command": ["sh", "-c", cmd],
                }
            ],
        },
    }


def _no_signal_pod_manifest(name: str, namespace: str) -> dict:  # type: ignore[type-arg]
    """Busybox pod that never writes the signal file (used for timeout tests)."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": _SENTINEL_TEST_LABEL},
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "hyperflow",
                    "image": "busybox:1.36",
                    "command": ["sleep", "3600"],
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _deploy_and_wait(manifest: dict, namespace: str, pod_name: str) -> None:  # type: ignore[type-arg]
    kubectl = Kubectl()
    await kubectl.apply_json(manifest)
    await kubectl.wait_for_pod(
        namespace=namespace,
        label_selector=f"app={_SENTINEL_TEST_LABEL}",
        timeout=300,
    )


@pytest_asyncio.fixture
async def engine_pod_success(
    kind_cluster,  # noqa: ANN001 — ensures kubectl context is set
    test_namespace: str,
) -> AsyncGenerator[str, None]:
    """Pod with container 'hyperflow' that writes exit code 0 after 3 s."""
    pod_name = "engine-success"
    await _deploy_and_wait(
        _engine_pod_manifest(pod_name, test_namespace, delay_seconds=3, exit_code=0),
        test_namespace,
        pod_name,
    )
    yield pod_name


@pytest_asyncio.fixture
async def engine_pod_failure(
    kind_cluster,  # noqa: ANN001
    test_namespace: str,
) -> AsyncGenerator[str, None]:
    """Pod with container 'hyperflow' that writes exit code 1 after 3 s."""
    pod_name = "engine-failure"
    await _deploy_and_wait(
        _engine_pod_manifest(pod_name, test_namespace, delay_seconds=3, exit_code=1),
        test_namespace,
        pod_name,
    )
    yield pod_name


@pytest_asyncio.fixture
async def engine_pod_no_signal(
    kind_cluster,  # noqa: ANN001
    test_namespace: str,
) -> AsyncGenerator[str, None]:
    """Pod with container 'hyperflow' that never writes the signal file."""
    pod_name = "engine-nosignal"
    await _deploy_and_wait(
        _no_signal_pod_manifest(pod_name, test_namespace),
        test_namespace,
        pod_name,
    )
    yield pod_name


def _make_sentinel(
    pod_name: str,
    namespace: str,
    *,
    poll_interval: int = 5,
    timeout: int = 60,
) -> Sentinel:
    context = MonitoringContext(
        namespace=namespace,
        engine_pod_name=pod_name,
        engine_container="hyperflow",
        task_inventory=[
            TaskSpec(name="task-a", task_type="samtools"),
            TaskSpec(name="task-b", task_type="samtools"),
        ],
    )
    settings = SentinelSettings(
        poll_interval=poll_interval,
        timeout=timeout,
    )
    return Sentinel(context, settings)


# ---------------------------------------------------------------------------
# Sentinel-level integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSentinelOnKind:
    @pytest.mark.asyncio
    async def test_detects_exit_code_zero(
        self,
        engine_pod_success: str,
        test_namespace: str,
    ) -> None:
        """Sentinel reads the signal file written by the engine pod (exit 0)."""
        sentinel = _make_sentinel(engine_pod_success, test_namespace)
        summary = await asyncio.wait_for(sentinel.watch(), timeout=90)

        assert summary.exit_code == 0
        assert not summary.timed_out
        assert not summary.mass_failure

    @pytest.mark.asyncio
    async def test_detects_exit_code_nonzero(
        self,
        engine_pod_failure: str,
        test_namespace: str,
    ) -> None:
        """Sentinel reads the signal file and surfaces exit code 1."""
        sentinel = _make_sentinel(engine_pod_failure, test_namespace)
        summary = await asyncio.wait_for(sentinel.watch(), timeout=90)

        assert summary.exit_code == 1
        assert not summary.timed_out

    @pytest.mark.asyncio
    async def test_times_out_when_no_signal(
        self,
        engine_pod_no_signal: str,
        test_namespace: str,
    ) -> None:
        """Sentinel times out if the engine never writes the signal file."""
        # Very short timeout (8 s) so the test finishes quickly.
        sentinel = _make_sentinel(
            engine_pod_no_signal, test_namespace, poll_interval=2, timeout=8
        )
        summary = await asyncio.wait_for(sentinel.watch(), timeout=25)

        assert summary.timed_out
        assert summary.exit_code is None

    @pytest.mark.asyncio
    async def test_completion_report_is_queued(
        self,
        engine_pod_success: str,
        test_namespace: str,
    ) -> None:
        """At least one report (the COMPLETION event) is placed in the queue."""
        sentinel = _make_sentinel(engine_pod_success, test_namespace)
        await asyncio.wait_for(sentinel.watch(), timeout=90)

        assert not sentinel.reports.empty()

    @pytest.mark.asyncio
    async def test_summary_total_tasks_reflects_inventory(
        self,
        engine_pod_success: str,
        test_namespace: str,
    ) -> None:
        """WatchSummary.total_tasks matches the MonitoringContext task inventory."""
        sentinel = _make_sentinel(engine_pod_success, test_namespace)
        summary = await asyncio.wait_for(sentinel.watch(), timeout=90)

        # Inventory has 2 tasks (task-a, task-b)
        assert summary.total_tasks == 2


# ---------------------------------------------------------------------------
# Full Phase 9 integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRunMonitoringPhaseOnKind:
    @pytest.mark.asyncio
    async def test_phase_completes_on_exit_zero(
        self,
        engine_pod_success: str,
        test_namespace: str,
    ) -> None:
        """run_monitoring_phase sets workflow_status='completed' for exit code 0."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace=test_namespace,
            engine_pod_name=engine_pod_success,
        )
        settings = ConductorSettings(
            monitor_poll_interval=5,
            monitor_timeout=60,
        )

        result = await asyncio.wait_for(
            run_monitoring_phase(state, settings), timeout=90
        )

        assert result.workflow_status == "completed"
        assert result.task_completion_count >= 0

    @pytest.mark.asyncio
    async def test_phase_fails_on_exit_nonzero(
        self,
        engine_pod_failure: str,
        test_namespace: str,
    ) -> None:
        """run_monitoring_phase sets workflow_status='failed' for exit code 1."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace=test_namespace,
            engine_pod_name=engine_pod_failure,
        )
        settings = ConductorSettings(
            monitor_poll_interval=5,
            monitor_timeout=60,
        )

        result = await asyncio.wait_for(
            run_monitoring_phase(state, settings), timeout=90
        )

        assert result.workflow_status == "failed"

    @pytest.mark.asyncio
    async def test_phase_timeout(
        self,
        engine_pod_no_signal: str,
        test_namespace: str,
    ) -> None:
        """run_monitoring_phase sets workflow_status='timeout' on sentinel timeout."""
        from workflow_conductor.phases.monitoring import run_monitoring_phase

        state = PipelineState(
            namespace=test_namespace,
            engine_pod_name=engine_pod_no_signal,
        )
        # Very short timeout — just enough to confirm the timeout path.
        settings = ConductorSettings(
            monitor_poll_interval=2,
            monitor_timeout=8,
        )

        result = await asyncio.wait_for(
            run_monitoring_phase(state, settings), timeout=25
        )

        assert result.workflow_status == "timeout"
