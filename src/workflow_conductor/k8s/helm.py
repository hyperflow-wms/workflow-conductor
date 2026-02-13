"""Async Helm wrapper using subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class HelmError(Exception):
    """Raised when a Helm command fails."""


class Helm:
    """Async wrapper around the helm CLI."""

    def __init__(self, kubeconfig: str = "") -> None:
        self.kubeconfig = kubeconfig

    async def _run(
        self,
        args: list[str],
        *,
        timeout: float = 600,
        check: bool = True,
    ) -> str:
        """Run a helm command and return stdout."""
        cmd = ["helm", *args]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])

        logger.debug("helm %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()

        if check and proc.returncode != 0:
            raise HelmError(
                f"helm {' '.join(args)} failed (rc={proc.returncode}): {stderr_str}"
            )
        return stdout_str

    async def upgrade_install(
        self,
        release: str,
        chart: str,
        *,
        namespace: str = "",
        values_files: list[str] | None = None,
        set_values: dict[str, str] | None = None,
        dependency_update: bool = False,
        wait: bool = True,
        timeout: str = "10m",
    ) -> str:
        """Run helm upgrade --install."""
        args = ["upgrade", "--install", release, chart]
        if namespace:
            args.extend(["--namespace", namespace])
        for vf in values_files or []:
            args.extend(["-f", vf])
        for k, v in (set_values or {}).items():
            args.extend(["--set", f"{k}={v}"])
        if dependency_update:
            args.append("--dependency-update")
        if wait:
            args.extend(["--wait", "--timeout", timeout])
        return await self._run(args, timeout=900)

    async def uninstall(
        self,
        release: str,
        *,
        namespace: str = "",
    ) -> str:
        """Run helm uninstall."""
        args = ["uninstall", release]
        if namespace:
            args.extend(["--namespace", namespace])
        return await self._run(args, check=False)

    async def release_exists(
        self,
        release: str,
        *,
        namespace: str = "",
    ) -> bool:
        """Check if a Helm release exists."""
        args = ["list", "--output", "json"]
        if namespace:
            args.extend(["--namespace", namespace])
        output = await self._run(args)
        releases: list[dict[str, Any]] = json.loads(output) if output else []
        return any(r.get("name") == release for r in releases)

    async def list_releases(
        self,
        *,
        namespace: str = "",
    ) -> list[dict[str, Any]]:
        """List Helm releases."""
        args = ["list", "--output", "json"]
        if namespace:
            args.extend(["--namespace", namespace])
        output = await self._run(args)
        return json.loads(output) if output else []
