"""Unit tests for the data preparation phase."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineState, WorkflowPlan


def _make_state(**overrides: object) -> PipelineState:
    """Create a state as if provisioning has run."""
    defaults = dict(
        workflow_plan=WorkflowPlan(
            chromosomes=["1", "2"],
            populations=["EUR"],
            parallelism=10,
            description="test",
        ),
        namespace="wf-1000g-20260217-120000",
        cluster_ready=True,
        engine_pod_name="engine-pod-123",
    )
    defaults.update(overrides)
    return PipelineState(**defaults)  # type: ignore[arg-type]


class TestDataPreparationPhase:
    @pytest.mark.asyncio
    async def test_decompresses_data_files(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state()
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    "",  # decompress command
                    "1:5000\n2:6000",  # scan command
                ]
            )

            result = await run_data_preparation_phase(state, settings)

        # First exec call is the decompress command
        decompress_call = kubectl.exec_in_pod.call_args_list[0]
        assert "gunzip" in decompress_call.args[1][2]
        assert decompress_call.kwargs["container"] == "hyperflow"
        assert len(result.chromosome_data) == 2

    @pytest.mark.asyncio
    async def test_scans_row_counts(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state()
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    "",  # decompress
                    "1:12345\n2:67890",  # scan
                ]
            )

            result = await run_data_preparation_phase(state, settings)

        assert len(result.chromosome_data) == 2
        assert result.chromosome_data[0].chromosome == "1"
        assert result.chromosome_data[0].row_count == 12345
        assert result.chromosome_data[0].vcf_file == "ALL.chr1.250000.vcf"
        assert result.chromosome_data[1].chromosome == "2"
        assert result.chromosome_data[1].row_count == 67890

    @pytest.mark.asyncio
    async def test_raises_for_missing_chromosomes(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(
                chromosomes=["22"],
                populations=["EUR"],
            ),
        )
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="not available in data container"):
            await run_data_preparation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_requires_engine_pod(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(engine_pod_name="")
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="engine pod not set"):
            await run_data_preparation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_requires_namespace(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(namespace="")
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="namespace not set"):
            await run_data_preparation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_requires_chromosomes_in_plan(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(chromosomes=[], populations=["EUR"]),
        )
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="No chromosomes specified"):
            await run_data_preparation_phase(state, settings)
