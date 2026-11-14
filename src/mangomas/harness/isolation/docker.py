"""Docker container isolation backend for the repair-loop oracle (spec-0044 / ADR-0040)."""

from __future__ import annotations

import logging
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

from mangomas.config.repair import (
    DEFAULT_REPAIR_ORACLE_IMAGE,
    DEFAULT_REPAIR_ORACLE_MEMORY_LIMIT,
    DEFAULT_REPAIR_ORACLE_PIDS_LIMIT,
    DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS,
    validate_cli_token,
)
from mangomas.errors import ToolExecutionError
from mangomas.harness.isolation.base import (
    IsolationResult,
    SubprocessRunner,
    resolve_sandbox_argv,
    sandbox_pythonpath,
    select_sandbox_env,
)

logger = logging.getLogger(__name__)

DOCKER_WORK_DIR: Final[str] = "/work"
DOCKER_TMPFS_SPEC: Final[str] = "/tmp:rw,noexec,nosuid,size=64m"  # noqa: S108 — container tmpfs mount spec, not a host temp file
DOCKER_CONTAINER_PYTHON: Final[str] = "python"
# Bounded termination check after a timeout: re-issue `docker kill` and poll
# `docker ps` until the container is gone, so a still-running oracle is never
# reported as killed.
DEFAULT_DOCKER_KILL_VERIFY_ATTEMPTS: Final[int] = 3
DEFAULT_DOCKER_KILL_VERIFY_INTERVAL_SECONDS: Final[float] = 0.5
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
        kill_verify_attempts: int = DEFAULT_DOCKER_KILL_VERIFY_ATTEMPTS,
        kill_verify_interval_seconds: float = DEFAULT_DOCKER_KILL_VERIFY_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        try:
            self._image = validate_cli_token(image, field_name="image")
            self._memory_limit = validate_cli_token(memory_limit, field_name="memory_limit")
        except ValueError as exc:
            raise ToolExecutionError(str(exc), tool_name="repair_sandbox") from exc
        self._pids_limit = pids_limit
        self._probe_timeout_seconds = probe_timeout_seconds
        self._runner = runner
        self._name_factory = name_factory
        self._kill_verify_attempts = max(1, kill_verify_attempts)
        self._kill_verify_interval_seconds = kill_verify_interval_seconds
        self._sleep = sleep

    def _run_subproc(self) -> SubprocessRunner:
        return subprocess.run if self._runner is None else self._runner

    def _docker_cmd(self, cmd: list[str]) -> subprocess.CompletedProcess[str] | None:
        """Run a short Docker CLI call under the probe budget; ``None`` if it could not run."""
        try:
            return self._run_subproc()(
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
                "Docker CLI call %r failed: %s",
                cmd,
                exc,
                extra={"event": "repair_oracle_docker_cli_failed", "backend": self.name},
            )
            return None

    def is_available(self) -> bool:
        """Return ``True`` when the Docker daemon is reachable and *image* exists locally."""
        probes = (
            ["docker", "info"],
            ["docker", "image", "inspect", self._image],
        )
        for cmd in probes:
            res = self._docker_cmd(cmd)
            if res is None:
                logger.debug(
                    "Docker isolation probe %r could not run",
                    cmd,
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
        cwd_str = str(cwd.resolve())
        if any(ch in cwd_str for ch in (",", '"', "\r", "\n", "\x00")):
            raise ToolExecutionError(
                f"Unsafe workspace path for Docker --mount specification: {cwd_str!r}",
                tool_name="repair_sandbox",
            )
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
        sandbox_env = select_sandbox_env(env)
        sandbox_env["PYTHONPATH"] = sandbox_pythonpath(DOCKER_WORK_DIR)
        for key, val in sorted(sandbox_env.items()):
            cmd.extend(["-e", f"{key}={val}"])
        cmd.append("--")
        cmd.append(self._image)
        cmd.extend(inner_argv)
        return cmd

    def _container_running(self, container_name: str) -> bool | None:
        """``True``/``False`` from ``docker ps``; ``None`` when the daemon cannot say."""
        res = self._docker_cmd(["docker", "ps", "-q", "--filter", f"name=^/{container_name}$"])
        if res is None or res.returncode != 0:
            return None
        return bool(res.stdout.strip())

    def _kill_container(self, container_name: str) -> bool:
        """Kill *container_name* and return ``True`` only once it is confirmed gone."""
        for attempt in range(1, self._kill_verify_attempts + 1):
            kill = self._docker_cmd(["docker", "kill", container_name])
            if self._container_running(container_name) is False:
                logger.warning(
                    "Killed timed-out repair-oracle container %s",
                    container_name,
                    extra={
                        "event": "repair_oracle_timeout_killed",
                        "container": container_name,
                        "attempt": attempt,
                        "kill_returncode": None if kill is None else kill.returncode,
                    },
                )
                return True
            if attempt < self._kill_verify_attempts:
                self._sleep(self._kill_verify_interval_seconds)
        logger.error(
            "Could not confirm termination of timed-out repair-oracle container %s after "
            "%d attempt(s); it may still be running (remove it with `docker rm -f %s`)",
            container_name,
            self._kill_verify_attempts,
            container_name,
            extra={
                "event": "repair_oracle_timeout_kill_failed",
                "container": container_name,
                "attempts": self._kill_verify_attempts,
            },
        )
        return False

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
            terminated = self._kill_container(container_name)
            suffix = "" if terminated else f"; container {container_name!r} may still be running"
            raise ToolExecutionError(
                f"Oracle subprocess exceeded timeout of {timeout_seconds}s{suffix}",
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
