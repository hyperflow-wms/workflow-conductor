"""Unit tests for the data preparation phase (all-tabix, no data container)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.config import ConductorSettings
from workflow_conductor.models import PipelineState, WorkflowPlan
from workflow_conductor.phases.data_preparation import (
    _build_tabix_job,
    _filter_commands_for_chromosomes,
)


def _make_state(**overrides: object) -> PipelineState:
    """Create a state as if provisioning has run."""
    defaults = dict(
        workflow_plan=WorkflowPlan(
            chromosomes=["1", "2"],
            populations=["EUR"],
            parallelism=10,
            description="test",
            download_commands=[
                (
                    "curl -sL https://ex.co/ALL.chr1.vcf.gz"
                    " | gunzip > /work_dir/ALL.chr1.250000.vcf"
                ),
                (
                    "curl -sL https://ex.co/ALL.chr2.vcf.gz"
                    " | gunzip > /work_dir/ALL.chr2.250000.vcf"
                ),
            ],
        ),
        namespace="wf-1000g-20260217-120000",
        cluster_ready=True,
        engine_pod_name="engine-pod-123",
    )
    defaults.update(overrides)
    return PipelineState(**defaults)  # type: ignore[arg-type]


class TestDataPreparationPhase:
    @pytest.mark.asyncio
    async def test_downloads_via_tabix_job(self) -> None:
        """All chromosomes download via K8s Job (no data container)."""
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state()
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.apply_json = AsyncMock(return_value="job created")
            kubectl.wait_for_job = AsyncMock(return_value="")
            kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    # scan VCF files
                    "1:5000:ALL.chr1.250000.vcf:ALL.chr1.ann.vcf\n"
                    "2:6000:ALL.chr2.250000.vcf:ALL.chr2.ann.vcf",
                    # extract VCF header
                    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG00096",
                ]
            )

            result = await run_data_preparation_phase(state, settings)

        # Should create tabix job
        kubectl.apply_json.assert_called_once()
        job_manifest = kubectl.apply_json.call_args.args[0]
        assert job_manifest["kind"] == "Job"
        assert job_manifest["metadata"]["name"] == "tabix-extract"

        # Should wait for the job
        kubectl.wait_for_job.assert_called_once()

        # Should scan VCF row counts
        assert len(result.chromosome_data) == 2
        assert result.chromosome_data[0].row_count == 5000
        assert result.chromosome_data[1].row_count == 6000

    @pytest.mark.asyncio
    async def test_extracts_vcf_header(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state()
        settings = ConductorSettings()

        vcf_header = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tHG00096\tHG00097"
        )

        with patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.apply_json = AsyncMock(return_value="job created")
            kubectl.wait_for_job = AsyncMock(return_value="")
            kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    "1:5000:ALL.chr1.250000.vcf:ALL.chr1.ann.vcf\n"
                    "2:6000:ALL.chr2.250000.vcf:ALL.chr2.ann.vcf",
                    vcf_header,
                ]
            )

            result = await run_data_preparation_phase(state, settings)

        assert result.vcf_header == vcf_header
        # Header extraction is the second exec_in_pod call
        header_call = kubectl.exec_in_pod.call_args_list[1]
        assert "grep" in header_call.args[1][2]
        assert "#CHROM" in header_call.args[1][2]

    @pytest.mark.asyncio
    async def test_scans_row_counts(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state()
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.apply_json = AsyncMock(return_value="job created")
            kubectl.wait_for_job = AsyncMock(return_value="")
            kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    "1:12345:ALL.chr1.250000.vcf:ALL.chr1.ann.vcf\n"
                    "2:67890:ALL.chr2.250000.vcf:ALL.chr2.ann.vcf",
                    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1",
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

    @pytest.mark.asyncio
    async def test_requires_download_commands(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(
                chromosomes=["1"],
                populations=["EUR"],
                download_commands=[],
            ),
        )
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="No download commands"):
            await run_data_preparation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_no_matching_commands_raises(self) -> None:
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(
                chromosomes=["22"],
                populations=["EUR"],
                download_commands=[
                    "curl -sL https://ex.co/ALL.chr11.vcf.gz | gunzip > ALL.chr11.vcf",
                ],
            ),
        )
        settings = ConductorSettings()

        with pytest.raises(ValueError, match="No download commands found"):
            await run_data_preparation_phase(state, settings)


class TestFilterCommands:
    """Tests for _filter_commands_for_chromosomes helper."""

    def test_filters_by_chromosome_dot(self) -> None:
        commands = [
            "curl -sL https://ex.co/ALL.chr1.vcf.gz | gunzip > ALL.chr1.250000.vcf",
            ("curl -sL https://ex.co/ALL.chr11.vcf.gz | gunzip > ALL.chr11.250000.vcf"),
            "curl -sL https://ex.co/ALL.chr2.vcf.gz | gunzip > ALL.chr2.250000.vcf",
        ]
        result = _filter_commands_for_chromosomes(commands, ["11"])
        assert len(result) == 1
        assert "chr11." in result[0]

    def test_filters_by_chromosome_space(self) -> None:
        """Tabix commands have 'chr11 ' (space before region spec)."""
        commands = [
            "tabix -h https://ex.co/ALL.chr11.vcf.gz 11:1000-2000 > output.vcf",
        ]
        result = _filter_commands_for_chromosomes(commands, ["11"])
        assert len(result) == 1

    def test_no_false_positives_chr1_vs_chr11(self) -> None:
        """chr1 filter should not match chr11."""
        commands = [
            ("curl -sL https://ex.co/ALL.chr11.vcf.gz | gunzip > ALL.chr11.250000.vcf"),
        ]
        result = _filter_commands_for_chromosomes(commands, ["1"])
        assert len(result) == 0

    def test_multiple_chromosomes(self) -> None:
        commands = [
            "curl -sL .../ALL.chr11.vcf.gz | gunzip > ALL.chr11.250000.vcf",
            "curl -sL .../ALL.chr12.vcf.gz | gunzip > ALL.chr12.250000.vcf",
            "curl -sL .../ALL.chr1.vcf.gz | gunzip > ALL.chr1.250000.vcf",
        ]
        result = _filter_commands_for_chromosomes(commands, ["11", "12"])
        assert len(result) == 2

    def test_empty_commands(self) -> None:
        assert _filter_commands_for_chromosomes([], ["11"]) == []

    def test_empty_chromosomes(self) -> None:
        commands = ["curl -sL .../ALL.chr11.vcf.gz | gunzip > out.vcf"]
        assert _filter_commands_for_chromosomes(commands, []) == []


class TestBuildTabixJob:
    """Tests for _build_tabix_job helper."""

    def test_job_structure(self) -> None:
        job = _build_tabix_job(
            commands=["echo hello", "echo world"],
            namespace="test-ns",
            image="broadinstitute/gatk:4.4.0.0",
        )
        assert job["apiVersion"] == "batch/v1"
        assert job["kind"] == "Job"
        assert job["metadata"]["name"] == "tabix-extract"
        assert job["metadata"]["namespace"] == "test-ns"

    def test_job_container_config(self) -> None:
        job = _build_tabix_job(
            commands=["echo hello"],
            namespace="test-ns",
            image="broadinstitute/gatk:4.4.0.0",
        )
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "broadinstitute/gatk:4.4.0.0"
        assert container["workingDir"] == "/work_dir"
        assert container["command"] == ["/bin/sh", "-c", "echo hello"]

    def test_job_nfs_pvc_mount(self) -> None:
        job = _build_tabix_job(
            commands=["echo hello"],
            namespace="test-ns",
            image="test:1.0",
        )
        volumes = job["spec"]["template"]["spec"]["volumes"]
        assert volumes[0]["persistentVolumeClaim"]["claimName"] == "nfs"
        mounts = job["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        assert mounts[0]["mountPath"] == "/work_dir"

    def test_job_commands_joined(self) -> None:
        job = _build_tabix_job(
            commands=["cmd1", "cmd2", "cmd3"],
            namespace="test-ns",
            image="test:1.0",
        )
        script = job["spec"]["template"]["spec"]["containers"][0]["command"][2]
        assert script == "cmd1 && cmd2 && cmd3"

    def test_job_no_retry(self) -> None:
        job = _build_tabix_job(
            commands=["echo hello"],
            namespace="test-ns",
            image="test:1.0",
        )
        assert job["spec"]["backoffLimit"] == 0
        assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
