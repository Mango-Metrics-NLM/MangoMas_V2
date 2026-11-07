"""Unisolated host subprocess backend for the repair-loop oracle (default ``none``)."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from mangomas.errors import ToolExecutionError
from mangomas.harness.isolation.base import (
    IsolationResult,
    SubprocessRunner,
    resolve_sandbox_argv,
)

logger = logging.getLogger(__name__)


class NoIsolationBackend:
    """Host subprocess backend that preserves legacy behaviour and logs a warning."""

    name: str = "none"

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        interpreter: str = sys.executable,
    ) -> None:
        self._runner = runner
        self._interpreter = interpreter

    def is_available(self) -> bool:
        """Host subprocess execution is always available."""
        return True

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> IsolationResult:
        """Run *argv* on the host after emitting an unisolated-oracle warning."""
        logger.warning(
            "Repair-sandbox oracle running WITHOUT OS-enforced isolation (backend='none')",
            extra={"event": "repair_oracle_unisolated", "backend": self.name},
        )
        resolved_argv = resolve_sandbox_argv(
            argv,
            interpreter=self._interpreter,
            backend_name=self.name,
            allow_host_interpreter=True,
        )
        runner = subprocess.run if self._runner is None else self._runner
        try:
            completed = runner(
                resolved_argv,
                cwd=cwd.resolve(),
                env=dict(env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
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
