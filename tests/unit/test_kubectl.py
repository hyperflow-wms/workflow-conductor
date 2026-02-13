"""Unit tests for Kubectl wrapper — mocked subprocess."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from workflow_conductor.k8s.kubectl import Kubectl, KubectlError


@pytest.fixture
def kubectl() -> Kubectl:
    return Kubectl()


def _mock_proc(stdout: str = "", stderr: str = "", rc: int = 0) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate.return_value = (stdout.encode(), stderr.encode())
    proc.returncode = rc
    return proc


class TestKubectlRun:
    @pytest.mark.asyncio
    async def test_run_success(self, kubectl: Kubectl) -> None:
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc("ok")):
            result = await kubectl._run(["version", "--client"])
            assert result == "ok"

    @pytest.mark.asyncio
    async def test_run_failure_raises(self, kubectl: Kubectl) -> None:
        with (
            patch(
                "asyncio.create_subprocess_exec",
                return_value=_mock_proc(stderr="not found", rc=1),
            ),
            pytest.raises(KubectlError, match="not found"),
        ):
            await kubectl._run(["get", "nonexistent"])


class TestGetJson:
    @pytest.mark.asyncio
    async def test_get_pods(self, kubectl: Kubectl) -> None:
        pod_list = json.dumps({"items": [{"metadata": {"name": "pod-1"}}]})
        with patch("asyncio.create_subprocess_exec", return_value=_mock_proc(pod_list)):
            result = await kubectl.get_json("pods", namespace="default")
            assert result["items"][0]["metadata"]["name"] == "pod-1"

    @pytest.mark.asyncio
    async def test_get_with_label_selector(self, kubectl: Kubectl) -> None:
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc("{}")
        ) as mock_exec:
            await kubectl.get_json(
                "pods",
                namespace="ns",
                label_selector="app=hyperflow",
            )
            call_args = mock_exec.call_args[0]
            assert "-l" in call_args
            assert "app=hyperflow" in call_args


class TestWaitForPod:
    @pytest.mark.asyncio
    async def test_wait_returns_pod_name(self, kubectl: Kubectl) -> None:
        # First call: wait, second call: get pod name
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[
                _mock_proc("condition met"),
                _mock_proc("hyperflow-engine-abc123"),
            ],
        ):
            name = await kubectl.wait_for_pod(
                namespace="ns",
                label_selector="component=hyperflow-engine",
            )
            assert name == "hyperflow-engine-abc123"


class TestCreateNamespace:
    @pytest.mark.asyncio
    async def test_create_namespace_args(self, kubectl: Kubectl) -> None:
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc("created")
        ) as mock_exec:
            await kubectl.create_namespace("test-ns")
            call_args = mock_exec.call_args[0]
            assert "create" in call_args
            assert "namespace" in call_args
            assert "test-ns" in call_args
            assert "--dry-run=client" in call_args


class TestCreateResourceQuota:
    @pytest.mark.asyncio
    async def test_quota_args(self, kubectl: Kubectl) -> None:
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc()
        ) as mock_exec:
            await kubectl.create_resource_quota(
                "hflow-requests",
                namespace="ns",
                hard_cpu="21",
                hard_memory="60Gi",
            )
            call_args = mock_exec.call_args[0]
            assert "quota" in call_args
            assert "--hard=requests.cpu=21,requests.memory=60Gi" in call_args


class TestDeleteNamespace:
    @pytest.mark.asyncio
    async def test_delete_with_no_wait(self, kubectl: Kubectl) -> None:
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc()
        ) as mock_exec:
            await kubectl.delete_namespace("test-ns")
            call_args = mock_exec.call_args[0]
            assert "--ignore-not-found" in call_args
            assert "--wait=false" in call_args


class TestLogs:
    @pytest.mark.asyncio
    async def test_logs_with_container(self, kubectl: Kubectl) -> None:
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc("log output")
        ) as mock_exec:
            result = await kubectl.logs(
                "pod-1",
                namespace="ns",
                container="hyperflow",
                tail=50,
            )
            call_args = mock_exec.call_args[0]
            assert "-c" in call_args
            assert "hyperflow" in call_args
            assert "--tail=50" in call_args
            assert result == "log output"
