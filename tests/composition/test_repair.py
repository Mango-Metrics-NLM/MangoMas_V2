"""Unit tests for ``mangomas.composition.repair.build_oracle_isolation`` (spec-0044 / ADR-0040)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mangomas.composition import build_oracle_isolation
from mangomas.config import RepairSettings
from mangomas.errors import ConfigError
from mangomas.harness.isolation import (
    DockerIsolationBackend,
    IsolationResult,
    NoIsolationBackend,
    OracleIsolationBackend,
    isolation_registry,
)


class _StubBackend:
    def __init__(self, name: str, *, available: bool) -> None:
        self.name = name
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def run(
        self,
        argv: object,
        *,
        cwd: Path,
        env: object,
        timeout_seconds: float,
    ) -> IsolationResult:
        del argv, cwd, env, timeout_seconds
        return IsolationResult(returncode=0, stdout="", stderr="", backend=self.name)


def test_build_oracle_isolation_defaults_to_none_backend(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        backend = build_oracle_isolation()
    assert isinstance(backend, NoIsolationBackend)
    assert backend.name == "none"
    assert any(
        getattr(rec, "event", None) == "repair_oracle_isolation_selected" for rec in caplog.records
    )


def test_build_oracle_isolation_required_returns_available_backend() -> None:
    with isolation_registry.scoped(
        "docker",
        lambda _cfg: _StubBackend("docker", available=True),
    ):
        backend = build_oracle_isolation(
            RepairSettings(oracle_isolation="required", oracle_backend="docker")
        )
    assert isinstance(backend, OracleIsolationBackend)
    assert backend.name == "docker"


def test_build_oracle_isolation_required_auto_falls_through_and_fails_closed() -> None:
    with (
        isolation_registry.scoped(
            "docker",
            lambda _cfg: _StubBackend("docker", available=False),
        ),
        pytest.raises(ConfigError, match="no isolating backend is available") as exc_info,
    ):
        build_oracle_isolation(RepairSettings(oracle_isolation="required", oracle_backend="auto"))
    assert "docker=unavailable" in (exc_info.value.detail or "")


def test_build_oracle_isolation_registry_builds_real_docker_backend() -> None:
    factory = isolation_registry.get("docker")
    backend = factory(RepairSettings(oracle_image="mangomas:oracle"))
    assert isinstance(backend, DockerIsolationBackend)
