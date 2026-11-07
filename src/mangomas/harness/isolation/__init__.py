"""Pluggable OS-isolation backends for the repair-loop oracle (spec-0044 / ADR-0040)."""

from __future__ import annotations

from mangomas.harness.isolation.base import (
    PYTHON_ARGV_TOKENS,
    SANDBOX_ENV_KEYS,
    IsolationResult,
    OracleIsolationBackend,
    SubprocessRunner,
    resolve_sandbox_argv,
    select_sandbox_env,
)
from mangomas.harness.isolation.docker import (
    DOCKER_CONTAINER_PYTHON,
    DOCKER_TMPFS_SPEC,
    DOCKER_WORK_DIR,
    DockerIsolationBackend,
)
from mangomas.harness.isolation.none import NoIsolationBackend
from mangomas.harness.isolation.registry import OracleIsolationFactory, isolation_registry

__all__ = [
    "DOCKER_CONTAINER_PYTHON",
    "DOCKER_TMPFS_SPEC",
    "DOCKER_WORK_DIR",
    "PYTHON_ARGV_TOKENS",
    "SANDBOX_ENV_KEYS",
    "DockerIsolationBackend",
    "IsolationResult",
    "NoIsolationBackend",
    "OracleIsolationBackend",
    "OracleIsolationFactory",
    "SubprocessRunner",
    "isolation_registry",
    "resolve_sandbox_argv",
    "select_sandbox_env",
]
