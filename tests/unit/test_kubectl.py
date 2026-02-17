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
    async def test_create_namespace_dry_run_then_apply(self, kubectl: Kubectl) -> None:
        dry_run_proc = _mock_proc("apiVersion: v1\nkind: Namespace")
        apply_proc = _mock_proc("namespace/test-ns created")
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[dry_run_proc, apply_proc],
        ) as mock_exec:
            result = await kubectl.create_namespace("test-ns")
            assert mock_exec.call_count == 2
            # First call: dry-run to generate YAML
            dry_run_args = mock_exec.call_args_list[0][0]
            assert "create" in dry_run_args
            assert "namespace" in dry_run_args
            assert "test-ns" in dry_run_args
            assert "--dry-run=client" in dry_run_args
            # Second call: apply via stdin
            apply_args = mock_exec.call_args_list[1][0]
            assert "apply" in apply_args
            assert "-f" in apply_args
            assert "-" in apply_args
            assert result == "namespace/test-ns created"


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


class TestGetNodes:
    @pytest.mark.asyncio
    async def test_returns_node_info(self, kubectl: Kubectl) -> None:
        node_json = json.dumps(
            {
                "items": [
                    {
                        "status": {
                            "capacity": {"cpu": "4", "memory": "8Gi"},
                            "nodeInfo": {"kubeletVersion": "v1.31.0"},
                        }
                    },
                    {
                        "status": {
                            "capacity": {"cpu": "4", "memory": "8Gi"},
                            "nodeInfo": {"kubeletVersion": "v1.31.0"},
                        }
                    },
                ]
            }
        )
        with patch(
            "asyncio.create_subprocess_exec", return_value=_mock_proc(node_json)
        ):
            result = await kubectl.get_nodes()
        assert result["node_count"] == 2
        assert result["total_cpu"] == 8
        assert result["total_memory_gb"] == 16.0
        assert result["k8s_version"] == "v1.31.0"


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


class TestCleanupPreviousRuns:
    @pytest.mark.asyncio
    async def test_deletes_released_pvs(self, kubectl: Kubectl) -> None:
        procs = [
            _mock_proc("pvc-aaa pvc-bbb"),  # get pv (Released)
            _mock_proc(),  # delete pv pvc-aaa
            _mock_proc(),  # delete pv pvc-bbb
            _mock_proc(""),  # get namespaces
            _mock_proc(""),  # get Terminating namespaces
            *[_mock_proc() for _ in range(6)],  # 6 cluster resources
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            await kubectl.cleanup_previous_runs("wf-1000g")
            calls = [c[0] for c in mock_exec.call_args_list]
            # Second and third calls should delete the released PVs
            assert "pvc-aaa" in calls[1]
            assert "pvc-bbb" in calls[2]

    @pytest.mark.asyncio
    async def test_skips_current_namespace(self, kubectl: Kubectl) -> None:
        procs = [
            _mock_proc(""),  # get pv (Released)
            _mock_proc("wf-1000g-old wf-1000g-current"),  # namespaces
            _mock_proc(),  # force-delete pods in old ns
            _mock_proc(),  # delete old ns
            _mock_proc(""),  # get Terminating namespaces
            *[_mock_proc() for _ in range(6)],  # cluster resources
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            await kubectl.cleanup_previous_runs(
                "wf-1000g", current_namespace="wf-1000g-current"
            )
            all_args = [" ".join(c[0]) for c in mock_exec.call_args_list]
            deleted_ns = [a for a in all_args if "delete namespace" in a]
            assert len(deleted_ns) == 1
            assert "wf-1000g-old" in deleted_ns[0]

    @pytest.mark.asyncio
    async def test_force_removes_terminating_namespaces(self, kubectl: Kubectl) -> None:
        ns_json = json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "wf-1000g-stuck"},
                "spec": {"finalizers": ["kubernetes"]},
            }
        )
        procs = [
            _mock_proc(""),  # get pv (Released)
            _mock_proc(""),  # get namespaces (none active)
            _mock_proc("wf-1000g-stuck"),  # get Terminating
            _mock_proc(),  # force-delete pods
            _mock_proc(ns_json),  # get namespace JSON
            _mock_proc(),  # replace --raw /finalize
            *[_mock_proc() for _ in range(6)],  # cluster resources
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            await kubectl.cleanup_previous_runs("wf-1000g")
            all_args = [" ".join(c[0]) for c in mock_exec.call_args_list]
            # Should force-delete pods
            pod_deletes = [a for a in all_args if "delete pods --all" in a]
            assert len(pod_deletes) == 1
            assert "wf-1000g-stuck" in pod_deletes[0]
            # Should call replace --raw with /finalize endpoint
            finalize_calls = [a for a in all_args if "finalize" in a]
            assert len(finalize_calls) == 1
            assert "/api/v1/namespaces/wf-1000g-stuck/finalize" in finalize_calls[0]

    @pytest.mark.asyncio
    async def test_deletes_all_cluster_scoped_resources(self, kubectl: Kubectl) -> None:
        procs = [
            _mock_proc(""),  # get pv
            _mock_proc(""),  # get namespaces
            _mock_proc(""),  # get Terminating
            *[_mock_proc() for _ in range(6)],  # 6 cluster resources
        ]
        with patch("asyncio.create_subprocess_exec", side_effect=procs) as mock_exec:
            await kubectl.cleanup_previous_runs("wf-1000g")
            all_args = [" ".join(c[0]) for c in mock_exec.call_args_list]
            resource_deletes = [a for a in all_args if "--ignore-not-found" in a]
            # Should delete 6 cluster-scoped resources
            assert len(resource_deletes) == 6
            resources = " ".join(resource_deletes)
            assert "storageclass/nfs" in resources
            assert "clusterrole/hf-ops-nfs-server-provisioner" in resources
            assert "hyperflow-worker-pool-operator" in resources


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
