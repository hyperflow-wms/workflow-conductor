"""Async kubectl wrapper using subprocess."""

from __future__ import annotations

import asyncio
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
        cmd = ["kubectl", *args]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])

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

        cmd = ["kubectl", *args]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])

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
        cmd = ["kubectl", "apply", "-f", "-"]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])

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
        """Create a namespace (idempotent)."""
        return await self._run(
            [
                "create",
                "namespace",
                namespace,
                "--dry-run=client",
                "-o",
                "yaml",
            ]
        )

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
