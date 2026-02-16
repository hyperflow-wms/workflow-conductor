"""Unit tests for the approval phase (Gate 2)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from workflow_conductor.models import (
    InfrastructureMeasurements,
    PipelinePhase,
    PipelineState,
    PipelineStatus,
    UserResponse,
    WorkflowPlan,
)

SAMPLE_WORKFLOW_JSON = {
    "name": "1000genome",
    "version": "1.0.0",
    "processes": [
        {"name": "individuals_chr22_1", "type": "dataflow"},
        {"name": "individuals_chr22_2", "type": "dataflow"},
        {"name": "individuals_merge_chr22", "type": "dataflow"},
        {"name": "sifting_chr22", "type": "dataflow"},
        {"name": "mutation_overlap_EUR_chr22", "type": "dataflow"},
        {"name": "frequency_EUR_chr22", "type": "dataflow"},
    ],
    "signals": [{"name": "sig1"}, {"name": "sig2"}, {"name": "sig3"}],
    "ins": [0],
    "outs": [2],
}


@pytest.fixture
def state_after_generation() -> PipelineState:
    return PipelineState(
        user_prompt="Analyze EUR chr22",
        current_phase=PipelinePhase.APPROVAL,
        user_approved_plan=True,
        workflow_plan=WorkflowPlan(
            chromosomes=["22"],
            populations=["EUR"],
            parallelism=10,
        ),
        infrastructure=InfrastructureMeasurements(
            namespace="wf-20260216",
            node_count=2,
            available_vcpus=8,
            memory_gb=16.0,
        ),
        workflow_json=SAMPLE_WORKFLOW_JSON,
    )


class TestApprovalPhase:
    @pytest.mark.asyncio
    async def test_auto_approve(self, state_after_generation: PipelineState) -> None:
        from workflow_conductor.phases.approval import run_approval_phase

        result = await run_approval_phase(state_after_generation, auto_approve=True)
        assert result.user_approved_execution is True

    @pytest.mark.asyncio
    async def test_user_approves(self, state_after_generation: PipelineState) -> None:
        with patch(
            "workflow_conductor.phases.approval.prompt_execution_gate",
            return_value=UserResponse(action="approve"),
        ):
            from workflow_conductor.phases.approval import run_approval_phase

            result = await run_approval_phase(
                state_after_generation, auto_approve=False
            )
        assert result.user_approved_execution is True

    @pytest.mark.asyncio
    async def test_user_aborts(self, state_after_generation: PipelineState) -> None:
        with patch(
            "workflow_conductor.phases.approval.prompt_execution_gate",
            return_value=UserResponse(action="abort"),
        ):
            from workflow_conductor.phases.approval import run_approval_phase

            result = await run_approval_phase(
                state_after_generation, auto_approve=False
            )
        assert result.user_approved_execution is False
        assert result.status == PipelineStatus.ABORTED

    @pytest.mark.asyncio
    async def test_raises_without_workflow_json(self) -> None:
        state = PipelineState(
            current_phase=PipelinePhase.APPROVAL,
            workflow_json=None,
        )
        from workflow_conductor.phases.approval import run_approval_phase

        with pytest.raises(ValueError, match="workflow_json"):
            await run_approval_phase(state, auto_approve=True)

    @pytest.mark.asyncio
    async def test_extracts_task_counts(
        self, state_after_generation: PipelineState
    ) -> None:
        from workflow_conductor.phases.approval import run_approval_phase

        result = await run_approval_phase(state_after_generation, auto_approve=True)
        assert result.total_task_count == 6
