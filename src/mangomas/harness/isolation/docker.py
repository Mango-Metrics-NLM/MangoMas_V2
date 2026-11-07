"""Docker container isolation backend for the repair-loop oracle (spec-0044 / ADR-0040)."""

from __future__ import annotations

import contextlib
import logging
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

from mangomas.config.repair import (
    DEFAULT_REPAIR_ORACLE_IMAGE,
    DEFAULT_REPAIR_ORACLE_MEMORY_LIMIT,
    DEFAULT_REPAIR_ORACLE_PIDS_LIMIT,
    DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS,
)
from mangomas.errors import ToolExecutionError
from mangomas.harness.isolation.base import (
    IsolationResult,
    SubprocessRunner,
    resolve_sandbox_argv,
    select_sandbox_env,
)

logger = logging.getLogger(__name__)

DOCKER_WORK_DIR: Final[str] = "/work"
DOCKER_TMPFS_SPEC: Final[str] = "/tmp:rw,noexec,nosuid,size=64m"  # noqa: S108 — container tmpfs mount spec, not a host temp file
DOCKER_CONTAINER_PYTHON: Final[str] = "python"
_CONTAINER_ID_HEX_CHARS: Final[int] = 12


def _default_container_name() -> str:
    return f"mangomas-oracle-{uuid.uuid4().hex[:_CONTAINER_ID_HEX_CHARS]}"


class DockerIsolationBackend:
    """Run the repair-loop oracle inside a network-disabled, read-only Docker container."""

    name: str = "docker"

    def __init__(
        self,
        *,
        image: str = DEFAULT_REPAIR_ORACLE_IMAGE,
        memory_limit: str = DEFAULT_REPAIR_ORACLE_MEMORY_LIMIT,
        pids_limit: int = DEFAULT_REPAIR_ORACLE_PIDS_LIMIT,
        probe_timeout_seconds: float = DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS,
        runner: SubprocessRunner | None = None,
        name_factory: Callable[[], str] = _default_container_name,
    ) -> None:
        self._image = image
        self._memory_limit = memory_limit
        self._pids_limit = pids_limit
        self._probe_timeout_seconds = probe_timeout_seconds
        self._runner = runner
        self._name_factory = name_factory

    def _run_subproc(self) -> SubprocessRunner:
        return subprocess.run if self._runner is None else self._runner

    def is_available(self) -> bool:
        """Return ``True`` when the Docker daemon is reachable and *image* exists locally."""
        runner = self._run_subproc()
        probes = (
            ["docker", "info"],
            ["docker", "image", "inspect", self._image],
        )
        for cmd in probes:
            try:
                res = runner(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._probe_timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.debug(
                    "Docker isolation probe %r failed: %s",
                    cmd,
                    exc,
                    extra={"event": "repair_oracle_backend_unavailable", "backend": self.name},
                )
                return False
            if res.returncode != 0:
                logger.debug(
                    "Docker isolation probe %r exited %d",
                    cmd,
                    res.returncode,
                    extra={"event": "repair_oracle_backend_unavailable", "backend": self.name},
                )
                return False
        return True

    def _build_docker_argv(
        self,
        container_name: str,
        inner_argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> list[str]:
        mount_spec = f"type=bind,src={cwd.resolve()},dst={DOCKER_WORK_DIR},readonly"
        cmd: list[str] = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            DOCKER_TMPFS_SPEC,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._pids_limit),
            "--memory",
            self._memory_limit,
            "--mount",
            mount_spec,
            "-w",
            DOCKER_WORK_DIR,
        ]
        for key, val in sorted(select_sandbox_env(env).items()):
            cmd.extend(["-e", f"{key}={val}"])
        cmd.append(self._image)
        cmd.extend(inner_argv)
        return cmd

    def _kill_container(self, container_name: str) -> None:
        runner = self._run_subproc()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            runner(
                ["docker", "kill", container_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._probe_timeout_seconds,
                check=False,
            )
        logger.warning(
            "Killed timed-out repair-oracle container %s",
            container_name,
            extra={"event": "repair_oracle_timeout_killed", "container": container_name},
        )

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> IsolationResult:
        """Run *argv* inside a restricted container with *cwd* mounted read-only at ``/work``."""
        inner_argv = resolve_sandbox_argv(
            argv,
            interpreter=DOCKER_CONTAINER_PYTHON,
            backend_name=self.name,
        )
        container_name = self._name_factory()
        docker_argv = self._build_docker_argv(container_name, inner_argv, cwd=cwd, env=env)
        runner = self._run_subproc()
        try:
            completed = runner(
                docker_argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._kill_container(container_name)
            raise ToolExecutionError(
                f"Oracle subprocess exceeded timeout of {timeout_seconds}s",
                tool_name="repair_sandbox",
            ) from exc
        except OSError as exc:
            raise ToolExecutionError(
                f"Oracle subprocess failed to start: {exc}",
                tool_name="repair_sandbox",
            ) from exc
        return IsolationResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            backend=self.name,
        )
