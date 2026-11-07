"""Registry of repair-loop oracle isolation backend factories (spec-0044 / ADR-0040)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from mangomas.config.repair import RepairSettings
from mangomas.harness.isolation.base import OracleIsolationBackend
from mangomas.harness.isolation.docker import DockerIsolationBackend
from mangomas.harness.isolation.none import NoIsolationBackend
from mangomas.harness.isolation.wsl import WslBwrapIsolationBackend
from mangomas.registry import Registry

OracleIsolationFactory: TypeAlias = Callable[[RepairSettings], OracleIsolationBackend]

isolation_registry: Registry[OracleIsolationFactory] = Registry("oracle_isolation")


def _build_none_backend(_cfg: RepairSettings) -> OracleIsolationBackend:
    return NoIsolationBackend()


def _build_docker_backend(cfg: RepairSettings) -> OracleIsolationBackend:
    return DockerIsolationBackend(
        image=cfg.oracle_image,
        memory_limit=cfg.oracle_memory_limit,
        pids_limit=cfg.oracle_pids_limit,
        probe_timeout_seconds=cfg.oracle_probe_timeout_seconds,
    )


def _build_wsl_backend(cfg: RepairSettings) -> OracleIsolationBackend:
    return WslBwrapIsolationBackend(
        distro=cfg.wsl_distro,
        python_executable=cfg.wsl_python,
        probe_timeout_seconds=cfg.oracle_probe_timeout_seconds,
    )


isolation_registry.register("none", _build_none_backend)
isolation_registry.register("docker", _build_docker_backend)
isolation_registry.register("wsl", _build_wsl_backend)

__all__ = [
    "OracleIsolationFactory",
    "isolation_registry",
]
