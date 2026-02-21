"""Integration tests: completion phase teardown and summary scenarios.

Tests all combinations of auto_teardown / demo / no_teardown flags,
release existence checks, teardown error handling, status mapping,
and ExecutionSummary construction.

No Kind cluster required — all Helm and kubectl calls are mocked.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineState, PipelineStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DISPLAY = [
    "workflow_conductor.phases.completion.display_phase_header",
    "workflow_conductor.phases.completion.display_completion_summary",
]


def _mock_kubectl() -> MagicMock:
    m = MagicMock()
    m.delete_namespace = AsyncMock()
    m.cleanup_previous_runs = AsyncMock()
    return m


def _mock_helm(all_exist: bool = True) -> MagicMock:
    m = MagicMock()
    m.release_exists = AsyncMock(return_value=all_exist)
    m.uninstall = AsyncMock()
    return m


def _state(
    *,
    namespace: str = "wf-1000g-test",
    workflow_status: str = "completed",
    completed_tasks: int = 5,
    total_tasks: int = 5,
    phase_timings: dict[str, float] | None = None,
) -> PipelineState:
    return PipelineState(
        namespace=namespace,
        workflow_status=workflow_status,
        task_completion_count=completed_tasks,
        total_task_count=total_tasks,
        phase_timings=phase_timings if phase_timings is not None else {"planning": 1.5, "provisioning": 60.0},
    )


async def _run(
    state: PipelineState,
    settings: ConductorSettings,
    helm: MagicMock,
    kubectl: MagicMock,
) -> PipelineState:
    from workflow_conductor.phases.completion import run_completion_phase

    with ExitStack() as stack:
        stack.enter_context(
            patch("workflow_conductor.phases.completion.Kubectl", return_value=kubectl)
        )
        stack.enter_context(
            patch("workflow_conductor.phases.completion.Helm", return_value=helm)
        )
        for p in _DISPLAY:
            stack.enter_context(patch(p))
        return await run_completion_phase(state, settings)


# ---------------------------------------------------------------------------
# Teardown flag combinations
# ---------------------------------------------------------------------------


class TestTeardownFlags:
    async def test_auto_teardown_uninstalls_all_releases(self) -> None:
        """auto_teardown=True, no_teardown=False → all 3 releases uninstalled."""
        settings = ConductorSettings(auto_teardown=True, no_teardown=False)
        helm = _mock_helm(all_exist=True)

        result = await _run(_state(), settings, helm, _mock_kubectl())

        assert helm.uninstall.await_count == 3
        assert result.teardown_completed is True

    async def test_demo_mode_triggers_teardown(self) -> None:
        """demo=True, auto_teardown=False → teardown still happens."""
        settings = ConductorSettings(demo=True, auto_teardown=False, no_teardown=False)
        helm = _mock_helm(all_exist=True)

        result = await _run(_state(), settings, helm, _mock_kubectl())

        assert helm.uninstall.await_count == 3
        assert result.teardown_completed is True

    async def test_no_teardown_flag_prevents_teardown(self) -> None:
        """no_teardown=True overrides auto_teardown and demo — teardown skipped."""
        settings = ConductorSettings(auto_teardown=True, demo=True, no_teardown=True)
        helm = _mock_helm()

        result = await _run(_state(), settings, helm, _mock_kubectl())

        helm.uninstall.assert_not_called()
        helm.release_exists.assert_not_called()
        assert result.teardown_completed is False

    async def test_default_settings_skip_teardown(self) -> None:
        """auto_teardown=False, demo=False → teardown skipped by default."""
        settings = ConductorSettings(auto_teardown=False, demo=False, no_teardown=False)
        helm = _mock_helm()

        result = await _run(_state(), settings, helm, _mock_kubectl())

        helm.uninstall.assert_not_called()
        assert result.teardown_completed is False

    async def test_empty_namespace_skips_teardown_even_with_auto(self) -> None:
        """If namespace is empty string teardown is skipped despite auto_teardown=True."""
        settings = ConductorSettings(auto_teardown=True)
        helm = _mock_helm()

        result = await _run(_state(namespace=""), settings, helm, _mock_kubectl())

        helm.uninstall.assert_not_called()
        assert result.teardown_completed is False


# ---------------------------------------------------------------------------
# Release existence checks
# ---------------------------------------------------------------------------


class TestReleaseExistence:
    async def test_absent_release_is_not_uninstalled(self) -> None:
        """release_exists=False for hf-data → uninstall not called for that release."""
        settings = ConductorSettings(auto_teardown=True)

        async def exists(name: str, *, namespace: str = "") -> bool:
            return name != "hf-data"

        helm = _mock_helm()
        helm.release_exists = AsyncMock(side_effect=exists)

        await _run(_state(), settings, helm, _mock_kubectl())

        uninstalled = [c.args[0] for c in helm.uninstall.await_args_list]
        assert "hf-data" not in uninstalled
        assert helm.uninstall.await_count == 2

    async def test_all_absent_skips_uninstalls_but_deletes_namespace(self) -> None:
        """All releases absent → no uninstall calls, but namespace deletion still runs."""
        settings = ConductorSettings(auto_teardown=True)
        helm = _mock_helm(all_exist=False)
        kubectl = _mock_kubectl()

        result = await _run(_state(), settings, helm, kubectl)

        helm.uninstall.assert_not_called()
        kubectl.delete_namespace.assert_awaited_once()
        assert result.teardown_completed is True

    async def test_releases_uninstalled_in_correct_order(self) -> None:
        """hf-run → hf-data → hf-ops: teardown order matters for dependencies."""
        settings = ConductorSettings(auto_teardown=True)
        helm = _mock_helm(all_exist=True)

        await _run(_state(), settings, helm, _mock_kubectl())

        uninstalled = [c.args[0] for c in helm.uninstall.await_args_list]
        assert uninstalled == ["hf-run", "hf-data", "hf-ops"]

    async def test_cleanup_previous_runs_called_after_namespace_deletion(self) -> None:
        """cleanup_previous_runs is called as the final teardown step."""
        settings = ConductorSettings(auto_teardown=True)
        kubectl = _mock_kubectl()

        await _run(_state(), settings, _mock_helm(all_exist=True), kubectl)

        kubectl.delete_namespace.assert_awaited_once()
        kubectl.cleanup_previous_runs.assert_awaited_once()


# ---------------------------------------------------------------------------
# Error handling during teardown
# ---------------------------------------------------------------------------


class TestTeardownErrors:
    async def test_helm_error_during_uninstall_does_not_raise(self) -> None:
        """HelmError during uninstall is swallowed — phase completes without exception."""
        from workflow_conductor.k8s.helm import HelmError

        settings = ConductorSettings(auto_teardown=True)
        helm = _mock_helm(all_exist=True)
        helm.uninstall = AsyncMock(side_effect=HelmError("helm: release not found"))

        result = await _run(_state(), settings, helm, _mock_kubectl())

        # Exception swallowed; teardown_completed not set
        assert result.teardown_completed is False

    async def test_kubectl_error_during_namespace_delete_does_not_raise(self) -> None:
        """KubectlError during namespace deletion is swallowed."""
        from workflow_conductor.k8s.kubectl import KubectlError

        settings = ConductorSettings(auto_teardown=True)
        kubectl = _mock_kubectl()
        kubectl.delete_namespace = AsyncMock(side_effect=KubectlError("not found"))

        result = await _run(_state(), settings, _mock_helm(all_exist=True), kubectl)

        assert result.teardown_completed is False

    async def test_partial_teardown_failure_sets_teardown_completed_false(self) -> None:
        """If first uninstall fails, teardown_completed remains False."""
        from workflow_conductor.k8s.helm import HelmError

        settings = ConductorSettings(auto_teardown=True)
        helm = _mock_helm(all_exist=True)
        # Only fail on the first uninstall call
        call_count = 0

        async def uninstall_side(name: str, *, namespace: str = "") -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HelmError(f"failed to uninstall {name}")

        helm.uninstall = AsyncMock(side_effect=uninstall_side)

        result = await _run(_state(), settings, helm, _mock_kubectl())

        assert result.teardown_completed is False


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


class TestStatusMapping:
    async def test_completed_workflow_status_maps_to_completed(self) -> None:
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(workflow_status="completed"), settings, _mock_helm(), _mock_kubectl()
        )
        assert result.status == PipelineStatus.COMPLETED

    async def test_failed_workflow_status_maps_to_failed(self) -> None:
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(workflow_status="failed"), settings, _mock_helm(), _mock_kubectl()
        )
        assert result.status == PipelineStatus.FAILED

    async def test_timeout_workflow_status_maps_to_failed(self) -> None:
        """Timeout is not success → FAILED."""
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(workflow_status="timeout"), settings, _mock_helm(), _mock_kubectl()
        )
        assert result.status == PipelineStatus.FAILED

    async def test_unknown_workflow_status_maps_to_failed(self) -> None:
        """Any unrecognised workflow_status defaults to FAILED."""
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(workflow_status=""), settings, _mock_helm(), _mock_kubectl()
        )
        assert result.status == PipelineStatus.FAILED

    async def test_status_set_regardless_of_teardown_flag(self) -> None:
        """Pipeline status is set even when teardown is skipped."""
        settings = ConductorSettings(auto_teardown=False, no_teardown=True)
        result = await _run(
            _state(workflow_status="completed"), settings, _mock_helm(), _mock_kubectl()
        )
        assert result.status == PipelineStatus.COMPLETED


# ---------------------------------------------------------------------------
# ExecutionSummary construction
# ---------------------------------------------------------------------------


class TestExecutionSummary:
    async def test_summary_reflects_task_counts_from_state(self) -> None:
        """ExecutionSummary carries completed_tasks and total_tasks from PipelineState."""
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(completed_tasks=8, total_tasks=10),
            settings,
            _mock_helm(),
            _mock_kubectl(),
        )
        assert result.execution_summary is not None
        assert result.execution_summary.completed_tasks == 8
        assert result.execution_summary.total_tasks == 10

    async def test_failed_tasks_derived_from_total_minus_completed(self) -> None:
        """failed_tasks = max(0, total - completed)."""
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(completed_tasks=3, total_tasks=10),
            settings,
            _mock_helm(),
            _mock_kubectl(),
        )
        assert result.execution_summary is not None
        assert result.execution_summary.failed_tasks == 7

    async def test_failed_tasks_clamped_to_zero(self) -> None:
        """If completed > total (edge case), failed_tasks is 0, not negative."""
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(completed_tasks=12, total_tasks=10),
            settings,
            _mock_helm(),
            _mock_kubectl(),
        )
        assert result.execution_summary is not None
        assert result.execution_summary.failed_tasks == 0

    async def test_total_runtime_sums_all_phase_timings(self) -> None:
        """total_runtime_seconds = sum of all values in phase_timings."""
        settings = ConductorSettings(auto_teardown=False)
        timings = {"planning": 2.5, "provisioning": 45.0, "monitoring": 120.0}
        result = await _run(
            _state(phase_timings=timings),
            settings,
            _mock_helm(),
            _mock_kubectl(),
        )
        assert result.execution_summary is not None
        assert result.execution_summary.total_runtime_seconds == pytest.approx(167.5)

    async def test_empty_phase_timings_gives_zero_runtime(self) -> None:
        """No phase timings → total_runtime_seconds = 0.0."""
        settings = ConductorSettings(auto_teardown=False)
        result = await _run(
            _state(phase_timings={}),
            settings,
            _mock_helm(),
            _mock_kubectl(),
        )
        assert result.execution_summary is not None
        assert result.execution_summary.total_runtime_seconds == 0.0

    async def test_summary_always_set_even_when_teardown_skipped(self) -> None:
        """ExecutionSummary is always built, regardless of teardown settings."""
        settings = ConductorSettings(auto_teardown=False, no_teardown=True)
        result = await _run(
            _state(completed_tasks=5, total_tasks=5),
            settings,
            _mock_helm(),
            _mock_kubectl(),
        )
        assert result.execution_summary is not None
        assert result.execution_summary.total_tasks == 5
