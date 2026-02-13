"""Unit tests for Helm wrapper — mocked subprocess."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.k8s.helm import Helm, HelmError


@pytest.fixture
def helm() -> Helm:
    return Helm()


def _mock_proc(stdout: str = "", stderr: str = "", rc: int = 0) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate.return_value = (stdout.encode(), stderr.encode())
    proc.returncode = rc
    return proc


class TestHelmRun:
    @pytest.mark.asyncio
    async def test_run_success(self, helm: Helm) -> None:
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc("ok")):
            result = await helm._run(["version"])
            assert result == "ok"

    @pytest.mark.asyncio
    async def test_run_failure_raises(self, helm: Helm) -> None:
        with (
            patch(
                "asyncio.create_subprocess_exec",
                return_value=_mock_proc(stderr="error", rc=1),
            ),
            pytest.raises(HelmError, match="error"),
        ):
            await helm._run(["bad-command"])

    @pytest.mark.asyncio
    async def test_run_with_kubeconfig(self) -> None:
        helm = Helm(kubeconfig="/tmp/kube.conf")
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc("ok")
        ) as mock_exec:
            await helm._run(["version"])
            call_args = mock_exec.call_args[0]
            assert "--kubeconfig" in call_args
            assert "/tmp/kube.conf" in call_args


class TestUpgradeInstall:
    @pytest.mark.asyncio
    async def test_basic_upgrade_install(self, helm: Helm) -> None:
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc("installed")
        ) as mock_exec:
            result = await helm.upgrade_install("my-release", "my-chart")
            call_args = mock_exec.call_args[0]
            assert "upgrade" in call_args
            assert "--install" in call_args
            assert "my-release" in call_args
            assert "my-chart" in call_args
            assert result == "installed"

    @pytest.mark.asyncio
    async def test_with_namespace_and_values(self, helm: Helm) -> None:
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc()
        ) as mock_exec:
            await helm.upgrade_install(
                "rel",
                "chart",
                namespace="ns",
                values_files=["/tmp/v1.yaml", "/tmp/v2.yaml"],
                set_values={"image": "foo:latest"},
                dependency_update=True,
            )
            call_args = mock_exec.call_args[0]
            assert "--namespace" in call_args
            assert "ns" in call_args
            assert "-f" in call_args
            assert "/tmp/v1.yaml" in call_args
            assert "--set" in call_args
            assert "image=foo:latest" in call_args
            assert "--dependency-update" in call_args


class TestUninstall:
    @pytest.mark.asyncio
    async def test_uninstall(self, helm: Helm) -> None:
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc()):
            result = await helm.uninstall("my-release", namespace="ns")
            assert isinstance(result, str)


class TestReleaseExists:
    @pytest.mark.asyncio
    async def test_exists_true(self, helm: Helm) -> None:
        releases = json.dumps([{"name": "hf-run"}, {"name": "hf-ops"}])
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(releases)):
            assert await helm.release_exists("hf-run") is True

    @pytest.mark.asyncio
    async def test_exists_false(self, helm: Helm) -> None:
        releases = json.dumps([{"name": "hf-ops"}])
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(releases)):
            assert await helm.release_exists("hf-run") is False

    @pytest.mark.asyncio
    async def test_exists_empty(self, helm: Helm) -> None:
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc("")):
            assert await helm.release_exists("hf-run") is False


class TestListReleases:
    @pytest.mark.asyncio
    async def test_list(self, helm: Helm) -> None:
        releases = json.dumps([{"name": "hf-run", "status": "deployed"}])
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(releases)):
            result = await helm.list_releases()
            assert len(result) == 1
            assert result[0]["name"] == "hf-run"
