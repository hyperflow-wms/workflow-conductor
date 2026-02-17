"""Async kubectl wrapper using subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class KubectlError(Exception):
    """Raised when a kubectl command fails."""


class Kubectl:
    """Async wrapper around the kubectl CLI."""

    def __init__(self, kubeconfig: str = "") -> None:
        self.kubeconfig = kubeconfig

    async def _run(
        self,
        args: list[str],
        *,
        timeout: float = 300,
        check: bool = True,
    ) -> str:
        """Run a kubectl command and return stdout."""
        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])
        cmd.extend(args)

        logger.debug("kubectl %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if check and proc.returncode != 0:
            raise KubectlError(
                f"kubectl {' '.join(args)} failed (rc={proc.returncode}): {stderr_str}"
            )
        return stdout_str

    async def get_json(
        self,
        resource: str,
        *,
        namespace: str = "",
        label_selector: str = "",
    ) -> Any:
        """Get a resource as parsed JSON."""
        args = ["get", resource, "-o", "json"]
        if namespace:
            args.extend(["-n", namespace])
        if label_selector:
            args.extend(["-l", label_selector])
        output = await self._run(args)
        return json.loads(output)

    async def apply_json(
        self,
        manifest: dict[str, Any],
        *,
        namespace: str = "",
    ) -> str:
        """Apply a JSON manifest via stdin."""
        args = ["apply", "-f", "-"]
        if namespace:
            args.extend(["-n", namespace])

        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])
        cmd.extend(args)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=json.dumps(manifest).encode()),
            timeout=60,
        )
        if proc.returncode != 0:
            raise KubectlError(f"kubectl apply failed: {stderr.decode().strip()}")
        return stdout.decode().strip()

    async def wait_for_ready(
        self,
        resource: str,
        *,
        namespace: str = "",
        timeout: int = 300,
    ) -> str:
        """Wait for a resource to become ready."""
        args = [
            "wait",
            "--for=condition=ready",
            resource,
            f"--timeout={timeout}s",
        ]
        if namespace:
            args.extend(["-n", namespace])
        return await self._run(args, timeout=timeout + 30)

    async def wait_for_delete(
        self,
        resource: str,
        *,
        namespace: str = "",
        label_selector: str = "",
        timeout: int = 60,
    ) -> str:
        """Wait for a resource to be deleted."""
        args = ["wait", "--for=delete", resource, f"--timeout={timeout}s"]
        if namespace:
            args.extend(["-n", namespace])
        if label_selector:
            args.extend(["-l", label_selector])
        return await self._run(args, timeout=timeout + 30, check=False)

    async def wait_for_pod(
        self,
        *,
        namespace: str,
        label_selector: str,
        timeout: int = 300,
    ) -> str:
        """Wait for a pod matching label to be ready and return its name."""
        await self._run(
            [
                "wait",
                "--for=condition=ready",
                "pod",
                "-l",
                label_selector,
                "-n",
                namespace,
                f"--timeout={timeout}s",
            ],
            timeout=timeout + 30,
        )
        output = await self._run(
            [
                "get",
                "pods",
                "-l",
                label_selector,
                "-n",
                namespace,
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ]
        )
        return output

    async def wait_for_job(
        self,
        job_name: str,
        *,
        namespace: str,
        timeout: int = 300,
    ) -> str:
        """Wait for a job to complete."""
        return await self._run(
            [
                "wait",
                "--for=condition=complete",
                f"job/{job_name}",
                "-n",
                namespace,
                f"--timeout={timeout}s",
            ],
            timeout=timeout + 30,
        )

    async def create_configmap_from_file(
        self,
        name: str,
        file_path: str,
        *,
        namespace: str,
        file_key: str = "",
    ) -> str:
        """Create/update a ConfigMap from a file (idempotent via dry-run + apply)."""
        from_file = (
            f"--from-file={file_key}={file_path}"
            if file_key
            else f"--from-file={file_path}"
        )
        dry_run_output = await self._run(
            [
                "create",
                "configmap",
                name,
                from_file,
                "-n",
                namespace,
                "--dry-run=client",
                "-o",
                "yaml",
            ]
        )
        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])
        cmd.extend(["apply", "-f", "-"])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=dry_run_output.encode()),
            timeout=60,
        )
        if proc.returncode != 0:
            raise KubectlError(f"configmap apply failed: {stderr.decode().strip()}")
        return stdout.decode().strip()

    async def create_namespace(self, namespace: str) -> str:
        """Create a namespace (idempotent via dry-run + apply)."""
        dry_run_output = await self._run(
            [
                "create",
                "namespace",
                namespace,
                "--dry-run=client",
                "-o",
                "yaml",
            ]
        )
        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])
        cmd.extend(["apply", "-f", "-"])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=dry_run_output.encode()),
            timeout=60,
        )
        if proc.returncode != 0:
            raise KubectlError(f"namespace creation failed: {stderr.decode().strip()}")
        return stdout.decode().strip()

    async def create_resource_quota(
        self,
        name: str,
        *,
        namespace: str,
        hard_cpu: str,
        hard_memory: str,
    ) -> str:
        """Create a ResourceQuota."""
        return await self._run(
            [
                "create",
                "quota",
                name,
                "-n",
                namespace,
                f"--hard=requests.cpu={hard_cpu},requests.memory={hard_memory}",
            ],
            check=False,
        )

    async def get_nodes(self) -> dict[str, Any]:
        """Query cluster nodes and return aggregate capacity info."""
        data = await self.get_json("nodes")
        items = data.get("items", [])
        total_cpu = 0
        total_memory_gb = 0.0
        k8s_version = ""
        for node in items:
            status = node.get("status", {})
            capacity = status.get("capacity", {})
            total_cpu += int(capacity.get("cpu", "0"))
            mem_str = capacity.get("memory", "0")
            if mem_str.endswith("Gi"):
                total_memory_gb += float(mem_str[:-2])
            elif mem_str.endswith("Ki"):
                total_memory_gb += float(mem_str[:-2]) / (1024 * 1024)
            elif mem_str.endswith("Mi"):
                total_memory_gb += float(mem_str[:-2]) / 1024
            else:
                with contextlib.suppress(ValueError):
                    total_memory_gb += float(mem_str) / (1024**3)
            node_info = status.get("nodeInfo", {})
            if not k8s_version:
                k8s_version = node_info.get("kubeletVersion", "")
        return {
            "node_count": len(items),
            "total_cpu": total_cpu,
            "total_memory_gb": total_memory_gb,
            "k8s_version": k8s_version,
        }

    async def delete_namespace(
        self,
        namespace: str,
        *,
        wait: bool = False,
    ) -> str:
        """Delete a namespace."""
        args = ["delete", "namespace", namespace, "--ignore-not-found"]
        if not wait:
            args.append("--wait=false")
        return await self._run(args, check=False)

    async def logs(
        self,
        pod: str,
        *,
        namespace: str,
        container: str = "",
        tail: int = 100,
    ) -> str:
        """Get pod logs."""
        args = ["logs", pod, "-n", namespace, f"--tail={tail}"]
        if container:
            args.extend(["-c", container])
        return await self._run(args, check=False)

    async def exec_in_pod(
        self,
        pod: str,
        command: list[str],
        *,
        namespace: str,
        container: str = "",
        timeout: float = 600,
    ) -> str:
        """Execute a command in a pod and return stdout."""
        args = ["exec", pod, "-n", namespace]
        if container:
            args.extend(["-c", container])
        args.append("--")
        args.extend(command)
        return await self._run(args, timeout=timeout)

    async def cp_to_pod(
        self,
        local_path: str,
        pod: str,
        remote_path: str,
        *,
        namespace: str,
        container: str = "",
    ) -> str:
        """Copy a local file to a pod."""
        args = ["cp", local_path, f"{pod}:{remote_path}", "-n", namespace]
        if container:
            args.extend(["-c", container])
        return await self._run(args, timeout=120)

    async def cleanup_previous_runs(
        self,
        namespace_prefix: str,
        *,
        current_namespace: str = "",
    ) -> None:
        """Deep cleanup of namespaces, PVs, and cluster-scoped resources.

        Prevents disk exhaustion on the NFS provisioner by cleaning up:
        1. Released PersistentVolumes (free disk space first)
        2. Old namespaces matching the prefix
        3. Namespaces stuck in Terminating state (force-remove finalizers)
        4. Orphaned cluster-scoped resources from hf-ops and hf-run
        """
        # Step 1: Delete PersistentVolumes in Released state (reclaim disk)
        pv_output = await self._run(
            [
                "get",
                "pv",
                "-o",
                'jsonpath={.items[?(@.status.phase=="Released")].metadata.name}',
            ],
            check=False,
        )
        for pv_name in pv_output.split():
            if pv_name.strip():
                logger.info("Deleting released PV: %s", pv_name)
                await self._run(
                    ["delete", "pv", pv_name, "--ignore-not-found"],
                    check=False,
                )

        # Step 2: Delete old namespaces
        # Force-delete all pods first (engine runs `while true; sleep 5`)
        # then delete namespace, then clear spec.finalizers on any stuck ones
        output = await self._run(
            ["get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}"],
            check=False,
        )
        for ns in output.split():
            if ns.startswith(namespace_prefix) and ns != current_namespace:
                logger.info("Cleaning up old namespace: %s", ns)
                await self._run(
                    [
                        "delete",
                        "pods",
                        "--all",
                        "-n",
                        ns,
                        "--force",
                        "--grace-period=0",
                    ],
                    check=False,
                )
                await self.delete_namespace(ns)

        # Step 3: Force-remove namespaces stuck in Terminating state
        # Namespace finalizers live at spec.finalizers (not metadata);
        # must use the /finalize API endpoint via kubectl replace --raw
        term_output = await self._run(
            [
                "get",
                "namespaces",
                "-o",
                'jsonpath={.items[?(@.status.phase=="Terminating")].metadata.name}',
            ],
            check=False,
        )
        for ns in term_output.split():
            if ns.startswith(namespace_prefix) and ns != current_namespace:
                logger.info("Force-removing stuck namespace: %s", ns)
                # Force-delete remaining pods first
                await self._run(
                    [
                        "delete",
                        "pods",
                        "--all",
                        "-n",
                        ns,
                        "--force",
                        "--grace-period=0",
                    ],
                    check=False,
                )
                # Clear spec.finalizers via the /finalize API
                ns_json = await self._run(
                    ["get", "namespace", ns, "-o", "json"],
                    check=False,
                )
                if ns_json:
                    ns_obj = json.loads(ns_json)
                    ns_obj.setdefault("spec", {})["finalizers"] = []
                    cmd = ["kubectl"]
                    if self.kubeconfig:
                        cmd.extend(["--kubeconfig", self.kubeconfig])
                    cmd.extend(
                        [
                            "replace",
                            "--raw",
                            f"/api/v1/namespaces/{ns}/finalize",
                            "-f",
                            "-",
                        ]
                    )
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(
                        proc.communicate(input=json.dumps(ns_obj).encode()),
                        timeout=30,
                    )

        # Step 4: Delete orphaned cluster-scoped resources
        for resource in [
            "clusterrole/hf-ops-nfs-server-provisioner",
            "clusterrole/hyperflow-worker-pool-operator-role-cluster",
            "clusterrolebinding/hf-ops-nfs-server-provisioner",
            "clusterrolebinding/hyperflow-worker-pool-operator-rolebinding-cluster",
            "clusterrolebinding/serviceaccounts-cluster-admin-hf-run",
            "storageclass/nfs",
        ]:
            await self._run(
                ["delete", resource, "--ignore-not-found"],
                check=False,
            )
