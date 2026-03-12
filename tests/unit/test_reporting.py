"""Tests for experiment report generation."""

from __future__ import annotations

from unittest.mock import patch

from workflow_conductor.models import (
    ChromosomeData,
    ExecutionSummary,
    PipelineState,
    PipelineStatus,
    WorkflowPlan,
)
from workflow_conductor.reporting import (
    _estimate_cost,
    _format_bytes,
    generate_experiment_report,
)


class TestReportGeneration:
    def _make_state(self) -> PipelineState:
        """Build a minimal state with experiment data."""
        state = PipelineState(
            user_prompt="Analyze BRCA1 gene variants in the British population.",
            execution_id="20260312-140000",
            created_at="2026-03-12T14:00:00",
            status=PipelineStatus.COMPLETED,
            workflow_status="completed",
            namespace="wf-1000g-abc123",
            total_task_count=47,
            task_completion_count=47,
        )
        state.workflow_plan = WorkflowPlan(
            chromosomes=["17"],
            populations=["GBR"],
            parallelism=10,
            estimated_data_size_gb=0.5,
            description="BRCA1 region analysis",
        )
        state.chromosome_data = [
            ChromosomeData(
                chromosome="17",
                vcf_file="ALL.chr17.brca1.vcf",
                row_count=81271,
                annotation_file="ALL.chr17.brca1.annotation.vcf",
                file_size_bytes=45_000_000,
            ),
        ]
        state.phase_timings = {
            "routing": 0.1,
            "planning": 5.2,
            "validation": 0.0,
            "provisioning": 30.5,
            "data_preparation": 15.3,
            "generation": 2.1,
            "approval": 0.0,
            "deployment": 3.8,
            "monitoring": 180.0,
            "completion": 2.0,
        }
        state.llm_usage = {
            "planning": {
                "model": "gemini-2.0-flash",
                "provider": "google",
                "latency_ms": 4500,
                "tool_calls": 3,
                "input_tokens": 1200,
                "output_tokens": 800,
            },
            "monitoring": {
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 600,
                "output_tokens": 240,
                "api_calls": 12,
            },
        }
        state.planning_estimates = {
            "estimated_transfer_mb": 512,
            "estimated_parallelism": 10,
            "estimated_tasks": 50,
            "estimated_variants": {"17": 80000},
        }
        state.execution_summary = ExecutionSummary(
            total_tasks=47,
            completed_tasks=47,
            failed_tasks=0,
            total_runtime_seconds=239.0,
        )
        state.workflow_json = {
            "name": "1000genome-brca1",
            "processes": [
                {"name": f"individuals_{i}", "fun": "individuals"} for i in range(10)
            ]
            + [{"name": "individuals_merge_1", "fun": "individuals_merge"}]
            + [{"name": f"sifting_{i}", "fun": "sifting"} for i in range(10)]
            + [{"name": f"frequency_{i}", "fun": "frequency"} for i in range(13)]
            + [
                {"name": f"mutation_overlap_{i}", "fun": "mutation_overlap"}
                for i in range(13)
            ],
            "signals": [{"name": f"sig_{i}"} for i in range(46)],
        }
        return state

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_query(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "Analyze BRCA1 gene variants" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_phase_timings(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "180.0" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_grouped_timings(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "LLM" in report
        assert "Infrastructure" in report
        assert "Execution" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_llm_usage(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "gemini-2.0-flash" in report
        assert "4500" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_workflow_metrics(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "47" in report
        assert "46" in report
        assert "GBR" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_variant_counts(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "81,271" in report or "81271" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_starts_with_heading(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert report.startswith("# ")

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_has_tables(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "|" in report
        assert "---" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_version(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "Conductor Version:" in report
        assert "abc1234" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_cost_estimate(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "Est. cost ($)" in report
        assert "$" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_contains_data_sizes(self, _mock_git: object) -> None:
        report = generate_experiment_report(self._make_state())
        assert "Data Sizes" in report
        assert "42.9 MB" in report  # 45_000_000 bytes

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_estimated_vs_actual_comparison(self, _mock_git: object) -> None:
        """When planning estimates exist, show delta column."""
        report = generate_experiment_report(self._make_state())
        assert "Estimated (Phase 2)" in report
        assert "Delta (%)" in report
        assert "80,000" in report  # estimated
        assert "81,271" in report  # actual

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_task_count_adjustment(self, _mock_git: object) -> None:
        """When estimated tasks exist, show adjustment table."""
        report = generate_experiment_report(self._make_state())
        assert "Task Count Adjustment" in report
        assert "Advisory (Phase 2)" in report
        assert "Final (Phase 6)" in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_no_estimates_shows_simple_table(self, _mock_git: object) -> None:
        """Without planning estimates, use simple variant table."""
        state = self._make_state()
        state.planning_estimates = {}
        report = generate_experiment_report(state)
        assert "Actual (Phase 5)" in report
        assert "Delta (%)" not in report

    @patch("workflow_conductor.reporting._get_git_commit", return_value="abc1234")
    def test_report_generation_note(self, _mock_git: object) -> None:
        """Report notes that generation phase is deterministic."""
        report = generate_experiment_report(self._make_state())
        assert "deterministic MCP tool call" in report


class TestEstimateCost:
    def test_known_model(self) -> None:
        cost = _estimate_cost(
            {
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 1_000_000,
                "output_tokens": 100_000,
            }
        )
        # 1M * $3/M + 0.1M * $15/M = $3.00 + $1.50 = $4.50
        assert cost == "$4.5000"

    def test_unknown_model(self) -> None:
        cost = _estimate_cost(
            {
                "model": "unknown-model",
                "input_tokens": 1000,
                "output_tokens": 500,
            }
        )
        assert "N/A" in cost

    def test_no_tokens(self) -> None:
        cost = _estimate_cost({"model": "claude-sonnet-4-20250514"})
        assert cost == "N/A"


class TestFormatBytes:
    def test_zero(self) -> None:
        assert _format_bytes(0) == "N/A"

    def test_bytes(self) -> None:
        assert _format_bytes(500) == "500 B"

    def test_kilobytes(self) -> None:
        assert _format_bytes(2048) == "2.0 KB"

    def test_megabytes(self) -> None:
        assert _format_bytes(45_000_000) == "42.9 MB"

    def test_gigabytes(self) -> None:
        assert _format_bytes(2_500_000_000) == "2.33 GB"
