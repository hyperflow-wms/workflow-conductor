"""Unit tests for the data preparation phase."""

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
                    "1:5000:ALL.chr1.250000.vcf:ALL.chr1.ann.vcf\n"
                    "2:6000:ALL.chr2.250000.vcf:ALL.chr2.ann.vcf",  # scan
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
                    "1:12345:ALL.chr1.250000.vcf:ALL.chr1.ann.vcf\n"
                    "2:67890:ALL.chr2.250000.vcf:ALL.chr2.ann.vcf",  # scan
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


class TestRemoteExtraction:
    """Tests for remote chromosome extraction via K8s Job."""

    @pytest.mark.asyncio
    async def test_remote_chromosomes_create_tabix_job(self) -> None:
        """Remote chromosomes trigger apply_json + wait_for_job."""
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(
                chromosomes=["11"],
                populations=["EUR"],
                download_commands=[
                    (
                        "curl -sL https://ex.co/ALL.chr11.vcf.gz"
                        " | gunzip > ALL.chr11.250000.vcf"
                    ),
                ],
            ),
        )
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.apply_json = AsyncMock(return_value="job created")
            kubectl.wait_for_job = AsyncMock(return_value="")
            kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    "",  # stage shared files (columns.txt, populations)
                    "11:9999:ALL.chr11.250000.vcf:ALL.chr11.ann.vcf",  # scan
                ]
            )

            result = await run_data_preparation_phase(state, settings)

        # Should call exec_in_pod for staging, then scan
        # Should call apply_json with Job manifest
        kubectl.apply_json.assert_called_once()
        job_manifest = kubectl.apply_json.call_args.args[0]
        assert job_manifest["kind"] == "Job"
        assert job_manifest["metadata"]["name"] == "tabix-extract"
        assert job_manifest["spec"]["template"]["spec"]["containers"][0]["image"] == (
            "broadinstitute/gatk:4.4.0.0"
        )

        # Should wait for the job
        kubectl.wait_for_job.assert_called_once_with(
            "tabix-extract",
            namespace="wf-1000g-20260217-120000",
            timeout=600,
        )

        # Should still scan row counts
        assert len(result.chromosome_data) == 1
        assert result.chromosome_data[0].chromosome == "11"
        assert result.chromosome_data[0].row_count == 9999

    @pytest.mark.asyncio
    async def test_mixed_local_remote_chromosomes(self) -> None:
        """Both local decompress and remote Job paths run."""
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(
                chromosomes=["1", "11"],
                populations=["EUR"],
                download_commands=[
                    (
                        "curl -sL https://ex.co/ALL.chr11.vcf.gz"
                        " | gunzip > ALL.chr11.250000.vcf"
                    ),
                ],
            ),
        )
        settings = ConductorSettings()

        with patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl:
            kubectl = MockKubectl.return_value
            kubectl.exec_in_pod = AsyncMock(
                side_effect=[
                    "",  # decompress (local)
                    "1:5000:ALL.chr1.250000.vcf:ALL.chr1.ann.vcf\n"
                    "11:8000:ALL.chr11.250000.vcf:ALL.chr11.ann.vcf",  # scan (all)
                ]
            )
            kubectl.apply_json = AsyncMock(return_value="job created")
            kubectl.wait_for_job = AsyncMock(return_value="")

            result = await run_data_preparation_phase(state, settings)

        # Local decompress ran
        decompress_call = kubectl.exec_in_pod.call_args_list[0]
        assert "gunzip" in decompress_call.args[1][2]

        # Remote Job ran
        kubectl.apply_json.assert_called_once()
        kubectl.wait_for_job.assert_called_once()

        # Both chromosomes scanned
        assert len(result.chromosome_data) == 2

    @pytest.mark.asyncio
    async def test_remote_without_commands_raises(self) -> None:
        """ValueError when remote chromosomes requested but no download_commands."""
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(
                chromosomes=["22"],
                populations=["EUR"],
                download_commands=[],
            ),
        )
        settings = ConductorSettings()

        with (
            patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl,
            pytest.raises(ValueError, match="require remote extraction"),
        ):
            kubectl = MockKubectl.return_value
            kubectl.exec_in_pod = AsyncMock(return_value="")
            await run_data_preparation_phase(state, settings)

    @pytest.mark.asyncio
    async def test_remote_no_matching_commands_raises(self) -> None:
        """ValueError when commands exist but none match the chromosome."""
        from workflow_conductor.phases.data_preparation import (
            run_data_preparation_phase,
        )

        state = _make_state(
            workflow_plan=WorkflowPlan(
                chromosomes=["22"],
                populations=["EUR"],
                download_commands=[
                    (
                        "curl -sL https://ex.co/ALL.chr11.vcf.gz"
                        " | gunzip > ALL.chr11.250000.vcf"
                    ),
                ],
            ),
        )
        settings = ConductorSettings()

        with (
            patch("workflow_conductor.phases.data_preparation.Kubectl") as MockKubectl,
            pytest.raises(ValueError, match="No download commands found"),
        ):
            kubectl = MockKubectl.return_value
            kubectl.exec_in_pod = AsyncMock(return_value="")
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
