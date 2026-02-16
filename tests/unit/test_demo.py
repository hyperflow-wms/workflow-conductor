"""Unit tests for demo mode display functions."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from workflow_conductor.models import PipelinePhase
from workflow_conductor.ui.display import (
    _PHASE_EXPLANATIONS,
    demo_pause,
    display_phase_explanation,
    display_pipeline_banner,
    display_workflow_json_summary,
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


class TestPhaseExplanations:
    def test_all_phases_have_explanations(self) -> None:
        """Every PipelinePhase enum must have an explanation text."""
        for phase in PipelinePhase:
            assert phase in _PHASE_EXPLANATIONS, f"{phase.value} missing explanation"

    def test_explanation_panel_renders(self) -> None:
        output = _capture_output(display_phase_explanation, PipelinePhase.PLANNING)
        assert "Planning" in output
        assert "Workflow Composer" in output


class TestWorkflowJsonSummary:
    def test_summary_with_processes(self) -> None:
        workflow = {
            "processes": [
                {"name": "individuals_chr22_1"},
                {"name": "individuals_chr22_2"},
                {"name": "sifting_chr22_1"},
                {"name": "mutations_chr22_1"},
            ],
            "signals": [{"name": "s1"}, {"name": "s2"}, {"name": "s3"}],
        }
        output = _capture_output(display_workflow_json_summary, workflow)
        assert "4" in output  # total processes
        assert "individuals" in output
        assert "sifting" in output
        assert "mutations" in output
        assert "3" in output  # signals

    def test_summary_empty_workflow(self) -> None:
        output = _capture_output(
            display_workflow_json_summary, {"processes": [], "signals": []}
        )
        assert "0" in output


class TestDemoPause:
    def test_pause_calls_input(self) -> None:
        with patch("workflow_conductor.ui.display.console") as mock_console:
            demo_pause()
            mock_console.input.assert_called_once()

    def test_pause_custom_message(self) -> None:
        with patch("workflow_conductor.ui.display.console") as mock_console:
            demo_pause("Next phase?")
            call_args = mock_console.input.call_args[0][0]
            assert "Next phase?" in call_args


class TestDemoModeBanner:
    def test_banner_shows_demo_mode(self) -> None:
        output = _capture_output(display_pipeline_banner, "test prompt", demo=True)
        assert "DEMO MODE" in output

    def test_banner_normal_no_demo(self) -> None:
        output = _capture_output(display_pipeline_banner, "test prompt")
        assert "DEMO MODE" not in output
