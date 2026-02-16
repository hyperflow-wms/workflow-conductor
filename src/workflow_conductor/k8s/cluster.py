"""Kind cluster management."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


class ClusterError(Exception):
    """Raised when a cluster operation fails."""


class KindCluster:
    """Manage Kind clusters for development and testing."""

    def __init__(self, name: str, config: str = "") -> None:
        self.name = name
        self.config = config

    async def _run(
        self,
        args: list[str],
        *,
        timeout: float = 300,
        check: bool = True,
    ) -> str:
        cmd = ["kind", *args]
        logger.debug("kind %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if check and proc.returncode != 0:
            raise ClusterError(
                f"kind {' '.join(args)} failed (rc={proc.returncode}): {stderr_str}"
            )
        return stdout_str

    async def exists(self) -> bool:
        """Check if the cluster exists."""
        output = await self._run(["get", "clusters"], check=False)
        return self.name in output.splitlines()

    async def create(self) -> str:
        """Create the cluster if it doesn't exist."""
        if await self.exists():
            logger.info("Cluster '%s' already exists", self.name)
            return f"Cluster '{self.name}' already exists"
        args = ["create", "cluster", "--name", self.name]
        if self.config:
            args.extend(["--config", self.config])
        return await self._run(args, timeout=600)

    async def delete(self) -> str:
        """Delete the cluster."""
        return await self._run(
            ["delete", "cluster", "--name", self.name],
            check=False,
        )

    async def load_image(self, image: str) -> str:
        """Load a Docker image into the cluster."""
        return await self._run(
            ["load", "docker-image", image, "--name", self.name],
            timeout=120,
        )

    async def use_context(self) -> str:
        """Switch kubectl context to this cluster."""
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "config",
            "use-context",
            f"kind-{self.name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout.decode().strip()

    async def export_kubeconfig(self) -> str:
        """Export the Kind cluster's kubeconfig to a temp file.

        Returns the path to the temp file. Useful when the KUBECONFIG
        env var contains many merged configs that cause 'file name too long'
        errors.
        """
        output = await self._run(["get", "kubeconfig", "--name", self.name])
        fd, path = tempfile.mkstemp(prefix=f"kind-{self.name}-", suffix=".kubeconfig")
        with os.fdopen(fd, "w") as f:
            f.write(output)
        logger.info("Exported Kind kubeconfig to %s", path)
        return path
