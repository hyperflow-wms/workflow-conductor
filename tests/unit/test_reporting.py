"""Tests for experiment report generation."""

from __future__ import annotations

from workflow_conductor.models import (
    ChromosomeData,
    ExecutionSummary,
    PipelineState,
    PipelineStatus,
    WorkflowPlan,
)
from workflow_conductor.reporting import generate_experiment_report


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
        }
        state.execution_summary = ExecutionSummary(
            total_tasks=47, completed_tasks=47, failed_tasks=0, total_runtime_seconds=239.0,
        )
        state.workflow_json = {
            "name": "1000genome-brca1",
            "processes": [{"name": f"individuals_{i}", "fun": "individuals"} for i in range(10)]
                       + [{"name": "individuals_merge_1", "fun": "individuals_merge"}]
                       + [{"name": f"sifting_{i}", "fun": "sifting"} for i in range(10)]
                       + [{"name": f"frequency_{i}", "fun": "frequency"} for i in range(13)]
                       + [{"name": f"mutation_overlap_{i}", "fun": "mutation_overlap"} for i in range(13)],
            "signals": [{"name": f"sig_{i}"} for i in range(46)],
        }
        return state

    def test_report_contains_query(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert "Analyze BRCA1 gene variants" in report

    def test_report_contains_phase_timings(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert "180.0" in report

    def test_report_contains_grouped_timings(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert "LLM" in report
        assert "Infrastructure" in report
        assert "Execution" in report

    def test_report_contains_llm_usage(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert "gemini-2.0-flash" in report
        assert "4500" in report

    def test_report_contains_workflow_metrics(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert "47" in report
        assert "46" in report
        assert "GBR" in report

    def test_report_contains_variant_counts(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert "81,271" in report or "81271" in report

    def test_report_starts_with_heading(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert report.startswith("# ")

    def test_report_has_tables(self) -> None:
        report = generate_experiment_report(self._make_state())
        assert "|" in report
        assert "---" in report
