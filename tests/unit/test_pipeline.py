"""Unit tests for full pipeline wiring."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflow_conductor.app import run_pipeline
from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineStatus, UserResponse


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_dry_run_stops_after_validation(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = '{"description": "test"}'
        history_mock = MagicMock()
        history_mock.get.return_value = []
        mock_llm.history = history_mock

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            state = await run_pipeline(
                "test prompt",
                ConductorSettings(),
                dry_run=True,
                auto_approve=True,
            )

        assert state.status == PipelineStatus.COMPLETED
        assert state.intent_classification == "1000genome"
        assert state.workflow_plan is not None
        assert state.user_approved_plan is True
        # Deployment should NOT have run
        assert state.namespace == ""

    @pytest.mark.asyncio
    async def test_abort_at_gate1_stops_pipeline(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = '{"description": "test"}'
        history_mock = MagicMock()
        history_mock.get.return_value = []
        mock_llm.history = history_mock

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        with (
            patch(
                "workflow_conductor.phases.planning.Agent",
                return_value=mock_agent,
            ),
            patch(
                "workflow_conductor.phases.validation.prompt_validation_gate",
                return_value=UserResponse(action="abort"),
            ),
        ):
            state = await run_pipeline(
                "test prompt",
                ConductorSettings(),
            )

        assert state.status == PipelineStatus.ABORTED

    @pytest.mark.asyncio
    async def test_abort_at_gate2_stops_pipeline(self) -> None:
        """After provisioning+data prep+generation, user aborts at Gate 2."""
        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = json.dumps(
            {
                "description": "test",
                "chromosomes": ["1"],
                "populations": ["EUR"],
                "data_preparation": {
                    "steps": [
                        {
                            "commands": [
                                "curl -sL https://ex.co/ALL.chr1.vcf.gz"
                                " | gunzip > /work_dir/ALL.chr1.250000.vcf"
                            ]
                        }
                    ]
                },
            }
        )
        history_mock = MagicMock()
        history_mock.get.return_value = []
        mock_llm.history = history_mock

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        # Generation phase calls Agent.call_tool() directly (no LLM)
        # Response must include columns.txt when vcf_header is provided
        workflow_json = '{"name": "test", "processes": [{"name": "t1"}], "signals": []}'
        mcp_response = (
            "### columns.txt (1 individuals)\n"
            "```\n#CHROM\tPOS\tFORMAT\tS1\n```\n\n"
            f"### Workflow JSON\n```json\n{workflow_json}\n```"
        )
        tool_result_text = MagicMock()
        tool_result_text.text = mcp_response
        tool_result = MagicMock()
        tool_result.isError = False
        tool_result.content = [tool_result_text]

        gen_agent = MagicMock()
        gen_agent.__aenter__ = AsyncMock(return_value=gen_agent)
        gen_agent.__aexit__ = AsyncMock(return_value=False)
        gen_agent.call_tool = AsyncMock(return_value=tool_result)

        with (
            patch(
                "workflow_conductor.phases.planning.Agent",
                return_value=mock_agent,
            ),
            patch(
                "workflow_conductor.phases.generation.Agent",
                return_value=gen_agent,
            ),
            patch("workflow_conductor.phases.provisioning.Kubectl") as MockKubectl,
            patch("workflow_conductor.phases.provisioning.Helm") as MockHelm,
            patch("workflow_conductor.phases.provisioning.KindCluster") as MockKind,
            patch(
                "workflow_conductor.phases.approval.prompt_execution_gate",
                return_value=UserResponse(action="abort"),
            ),
            patch(
                "workflow_conductor.phases.data_preparation.Kubectl"
            ) as MockDataPrepKubectl,
        ):
            # Set up provisioning mocks
            kubectl = MockKubectl.return_value
            kubectl.create_namespace = AsyncMock()
            kubectl.create_resource_quota = AsyncMock()
            kubectl.cleanup_previous_runs = AsyncMock()
            kubectl.wait_for_pod = AsyncMock(return_value="engine-pod-123")
            kubectl.wait_for_job = AsyncMock()
            kubectl.get_nodes = AsyncMock(
                return_value={
                    "node_count": 2,
                    "total_cpu": 4,
                    "total_memory_gb": 8.0,
                    "k8s_version": "v1.30.0",
                }
            )

            helm = MockHelm.return_value
            helm.upgrade_install = AsyncMock()

            kind = MockKind.return_value
            kind.exists = AsyncMock(return_value=True)
            kind.use_context = AsyncMock()
            kind.export_kubeconfig = AsyncMock(return_value="/tmp/mock-kubeconfig.yaml")

            # Set up data preparation mocks (all-tabix flow)
            data_prep_kubectl = MockDataPrepKubectl.return_value
            data_prep_kubectl.apply_json = AsyncMock(return_value="job created")
            data_prep_kubectl.wait_for_job = AsyncMock(return_value="")
            data_prep_kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    "1:5000:ALL.chr1.250000.vcf:ALL.chr1.ann.vcf",
                    "#CHROM\tPOS\tFORMAT\tS1",
                ]
            )

            state = await run_pipeline(
                "test prompt",
                ConductorSettings(),
                auto_approve=True,
            )

        assert state.status == PipelineStatus.ABORTED
        assert state.user_approved_plan is True
        assert state.user_approved_execution is False
        # Provisioning ran (sets helm_release_name) but deployment did not
        assert state.helm_release_name == "hf-run"
        assert state.workflow_json_path == ""

    @pytest.mark.asyncio
    async def test_ten_phase_labels_in_results(self) -> None:
        """Dry run records phases 1-3 in phase_results."""
        mock_llm = AsyncMock()
        mock_llm.generate_str.return_value = '{"description": "test"}'
        history_mock = MagicMock()
        history_mock.get.return_value = []
        mock_llm.history = history_mock

        mock_agent = MagicMock()
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=False)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)

        with patch(
            "workflow_conductor.phases.planning.Agent",
            return_value=mock_agent,
        ):
            state = await run_pipeline(
                "test prompt",
                ConductorSettings(),
                dry_run=True,
                auto_approve=True,
            )

        phase_names = [r.phase.value for r in state.phase_results]
        assert "routing" in phase_names
        assert "planning" in phase_names
        assert "validation" in phase_names
