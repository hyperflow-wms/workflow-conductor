"""Unit tests for Rich display components."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from workflow_conductor.models import (
    ExecutionSummary,
    PipelinePhase,
    PipelineState,
    PipelineStatus,
    WorkflowPlan,
)
from workflow_conductor.ui.display import (
    display_completion_summary,
    display_error,
    display_phase_header,
    display_pipeline_banner,
    display_workflow_plan,
)


def _capture_output(fn: object, *args: object, **kwargs: object) -> str:
    """Capture Rich console output by temporarily replacing the console."""
    import workflow_conductor.ui.display as mod

    buf = StringIO()
    original = mod.console
    mod.console = Console(file=buf, force_terminal=True, width=120)
    try:
        fn(*args, **kwargs)  # type: ignore[operator]
    finally:
        mod.console = original
    return buf.getvalue()


class TestDisplayPipelineBanner:
    def test_banner_contains_prompt(self) -> None:
        output = _capture_output(display_pipeline_banner, "Analyze EUR, chr 22")
        assert "Analyze EUR, chr 22" in output

    def test_banner_contains_title(self) -> None:
        output = _capture_output(display_pipeline_banner, "test")
        assert "Workflow Conductor" in output

    def test_dry_run_banner(self) -> None:
        output = _capture_output(display_pipeline_banner, "test", dry_run=True)
        assert "DRY RUN" in output


class TestDisplayPhaseHeader:
    def test_routing_phase(self) -> None:
        output = _capture_output(display_phase_header, PipelinePhase.ROUTING)
        assert "Routing" in output

    def test_deployment_phase(self) -> None:
        output = _capture_output(display_phase_header, PipelinePhase.DEPLOYMENT)
        assert "Deployment" in output


class TestDisplayWorkflowPlan:
    def test_plan_with_all_fields(self) -> None:
        plan = WorkflowPlan(
            description="1000 Genomes EUR analysis",
            populations=["EUR"],
            chromosomes=["22"],
            parallelism=4,
            estimated_data_size_gb=2.5,
        )
        output = _capture_output(display_workflow_plan, plan)
        assert "EUR" in output
        assert "22" in output
        assert "4" in output
        assert "2.5" in output
        assert "Workflow Plan" in output

    def test_plan_minimal(self) -> None:
        plan = WorkflowPlan(description="Minimal plan")
        output = _capture_output(display_workflow_plan, plan)
        assert "Minimal plan" in output


class TestDisplayCompletionSummary:
    def test_summary_with_execution(self) -> None:
        state = PipelineState(
            execution_id="test-123",
            status=PipelineStatus.COMPLETED,
            namespace="hf-test",
            execution_summary=ExecutionSummary(
                total_tasks=10,
                completed_tasks=10,
                failed_tasks=0,
                total_runtime_seconds=120.0,
            ),
            phase_timings={"routing": 0.5, "planning": 10.0},
        )
        output = _capture_output(display_completion_summary, state)
        assert "test-123" in output
        assert "completed" in output
        assert "hf-test" in output
        assert "10" in output

    def test_summary_without_execution(self) -> None:
        state = PipelineState(
            execution_id="test-456",
            status=PipelineStatus.FAILED,
        )
        output = _capture_output(display_completion_summary, state)
        assert "test-456" in output
        assert "failed" in output


class TestDisplayError:
    def test_error_message(self) -> None:
        output = _capture_output(display_error, "something broke")
        assert "something broke" in output

    def test_error_with_phase(self) -> None:
        output = _capture_output(display_error, "timeout", phase="deployment")
        assert "deployment" in output
        assert "timeout" in output
