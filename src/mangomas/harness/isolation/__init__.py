"""Pluggable OS-isolation backends for the repair-loop oracle (spec-0044 / ADR-0040)."""

from __future__ import annotations

from mangomas.harness.isolation.base import (
    PYTHON_ARGV_TOKENS,
    SANDBOX_ENV_KEYS,
    IsolationResult,
    OracleIsolationBackend,
    SubprocessRunner,
    resolve_sandbox_argv,
    sandbox_pythonpath,
    select_sandbox_env,
)
from mangomas.harness.isolation.docker import (
    DEFAULT_DOCKER_KILL_VERIFY_ATTEMPTS,
    DEFAULT_DOCKER_KILL_VERIFY_INTERVAL_SECONDS,
    DOCKER_CONTAINER_PYTHON,
    DOCKER_TMPFS_SPEC,
    DOCKER_WORK_DIR,
    DockerIsolationBackend,
)
from mangomas.harness.isolation.none import NoIsolationBackend
from mangomas.harness.isolation.registry import OracleIsolationFactory, isolation_registry
from mangomas.harness.isolation.wsl import (
    WSL_WORK_DIR,
    WslBwrapIsolationBackend,
    windows_to_wsl_path,
)

__all__ = [
    "DEFAULT_DOCKER_KILL_VERIFY_ATTEMPTS",
    "DEFAULT_DOCKER_KILL_VERIFY_INTERVAL_SECONDS",
    "DOCKER_CONTAINER_PYTHON",
    "DOCKER_TMPFS_SPEC",
    "DOCKER_WORK_DIR",
    "PYTHON_ARGV_TOKENS",
    "SANDBOX_ENV_KEYS",
    "WSL_WORK_DIR",
    "DockerIsolationBackend",
    "IsolationResult",
    "NoIsolationBackend",
    "OracleIsolationBackend",
    "OracleIsolationFactory",
    "SubprocessRunner",
    "WslBwrapIsolationBackend",
    "isolation_registry",
    "resolve_sandbox_argv",
    "sandbox_pythonpath",
    "select_sandbox_env",
    "windows_to_wsl_path",
]
