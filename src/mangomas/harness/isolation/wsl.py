"""WSL2 + ``bubblewrap`` isolation backend for the repair-loop oracle (spec-0044 / ADR-0040)."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath
from typing import Final

from mangomas.config.repair import (
    DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS,
    DEFAULT_REPAIR_WSL_DISTRO,
    DEFAULT_REPAIR_WSL_PYTHON,
    validate_cli_token,
)
from mangomas.errors import ToolExecutionError
from mangomas.harness.isolation.base import (
    IsolationResult,
    SubprocessRunner,
    reject_candidate_python_startup_hooks,
    resolve_sandbox_argv,
    sandbox_pythonpath,
    select_sandbox_env,
)

logger = logging.getLogger(__name__)

WSL_WORK_DIR: Final[str] = "/work"
WSL_TMP_DIR: Final[str] = "/tmp"  # noqa: S108 — bwrap sandbox tmpfs mount point, not a host temp file
WSL_SAFE_PATH: Final[str] = "/usr/local/bin:/usr/bin:/bin"
WSL_RO_BINDS: Final[tuple[str, ...]] = ("/usr", "/lib", "/bin", "/etc")
WSL_RO_BIND_TRY: Final[tuple[str, ...]] = ("/lib64", "/opt")
_MIN_EXTENDED_DRIVE_LEN: Final[int] = 3


def windows_to_wsl_path(host_path: Path | str) -> str:
    """Convert a Windows drive path or POSIX path to a WSL ``/mnt/<drive>/...`` path."""
    raw = str(host_path)
    if raw[:4] in {"\\\\?\\", "//?/"}:
        after = raw[4:]
        if (
            len(after) >= _MIN_EXTENDED_DRIVE_LEN
            and after[0].isascii()
            and after[0].isalpha()
            and after[1] == ":"
            and after[2] in {"\\", "/"}
        ):
            raw = after
    if raw.startswith(("\\\\", "//")):
        raise ToolExecutionError(
            f"UNC or device paths are not supported by the WSL backend: {host_path!r}",
            tool_name="repair_sandbox",
        )
    win = PureWindowsPath(raw)
    if win.drive and ":" in win.drive:
        if not win.root:
            raise ToolExecutionError(
                f"Drive-relative Windows paths cannot be mapped to WSL: {raw!r}",
                tool_name="repair_sandbox",
            )
        drive_letter = win.drive.rstrip(":").lower()
        rest = "/".join(part for part in win.parts[1:] if part not in {"\\", "/"})
        return f"/mnt/{drive_letter}/{rest}" if rest else f"/mnt/{drive_letter}"
    return Path(raw).as_posix()


class WslBwrapIsolationBackend:
    """Run the repair-loop oracle inside a WSL2 ``bubblewrap --unshare-all`` namespace."""

    name: str = "wsl"

    def __init__(
        self,
        *,
        distro: str = DEFAULT_REPAIR_WSL_DISTRO,
        python_executable: str = DEFAULT_REPAIR_WSL_PYTHON,
        probe_timeout_seconds: float = DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS,
        runner: SubprocessRunner | None = None,
    ) -> None:
        try:
            self._distro = validate_cli_token(distro, field_name="distro")
            self._python_executable = validate_cli_token(
                python_executable, field_name="python_executable"
            )
        except ValueError as exc:
            raise ToolExecutionError(str(exc), tool_name="repair_sandbox") from exc
        self._probe_timeout_seconds = probe_timeout_seconds
        self._runner = runner

    def _run_subproc(self) -> SubprocessRunner:
        return subprocess.run if self._runner is None else self._runner

    def is_available(self) -> bool:
        """Return ``True`` when *distro* has ``bwrap`` and *python_executable* installed."""
        runner = self._run_subproc()
        probes = (
            ["wsl", "-d", self._distro, "--", "bwrap", "--version"],
            ["wsl", "-d", self._distro, "--", self._python_executable, "-c", "import sys"],
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
                    "WSL isolation probe %r failed: %s",
                    cmd,
                    exc,
                    extra={"event": "repair_oracle_backend_unavailable", "backend": self.name},
                )
                return False
            if res.returncode != 0:
                logger.debug(
                    "WSL isolation probe %r exited %d",
                    cmd,
                    res.returncode,
                    extra={"event": "repair_oracle_backend_unavailable", "backend": self.name},
                )
                return False
        return True

    def _build_wsl_bwrap_argv(
        self,
        inner_argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> list[str]:
        wsl_staging = windows_to_wsl_path(cwd.resolve())
        cmd: list[str] = [
            "wsl",
            "-d",
            self._distro,
            "--",
            "bwrap",
            "--unshare-all",
            "--die-with-parent",
        ]
        for mount in WSL_RO_BINDS:
            cmd.extend(["--ro-bind", mount, mount])
        for mount in WSL_RO_BIND_TRY:
            cmd.extend(["--ro-bind-try", mount, mount])
        cmd.extend(
            [
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                WSL_TMP_DIR,
                "--ro-bind",
                wsl_staging,
                WSL_WORK_DIR,
                "--chdir",
                WSL_WORK_DIR,
                "--clearenv",
                "--setenv",
                "PATH",
                WSL_SAFE_PATH,
            ]
        )
        sandbox_env = select_sandbox_env(env)
        sandbox_env["PYTHONPATH"] = sandbox_pythonpath(WSL_WORK_DIR)
        for key, val in sorted(sandbox_env.items()):
            cmd.extend(["--setenv", key, val])
        cmd.append("--")
        cmd.extend(inner_argv)
        return cmd

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> IsolationResult:
        """Run *argv* in a WSL2 ``bubblewrap`` sandbox with *cwd* bound read-only at ``/work``."""
        reject_candidate_python_startup_hooks(cwd)
        inner_argv = resolve_sandbox_argv(
            argv,
            interpreter=self._python_executable,
            backend_name=self.name,
        )
        wsl_argv = self._build_wsl_bwrap_argv(inner_argv, cwd=cwd, env=env)
        runner = self._run_subproc()
        try:
            completed = runner(
                wsl_argv,
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
