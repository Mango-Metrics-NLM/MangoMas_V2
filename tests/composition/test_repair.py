"""Unit tests for ``mangomas.composition.repair.build_oracle_isolation`` (spec-0044 / ADR-0040)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mangomas.composition import build_oracle_isolation, run_repair_oracle
from mangomas.config import RepairSettings, get_settings
from mangomas.errors import ConfigError
from mangomas.harness.isolation import (
    DockerIsolationBackend,
    IsolationResult,
    NoIsolationBackend,
    OracleIsolationBackend,
    isolation_registry,
)
from mangomas.harness.repair_sandbox import stage_candidate_file

_ISOLATION_ENV = "MANGOMAS_REPAIR__ORACLE_ISOLATION"
_BACKEND_ENV = "MANGOMAS_REPAIR__ORACLE_BACKEND"
_TIMEOUT_ENV = "MANGOMAS_REPAIR__ORACLE_TIMEOUT_SECONDS"
_CONFIGURED_TIMEOUT_SECONDS = 7.5
_ENV_TIMEOUT_SECONDS = 9.25
_CANDIDATE_REL = "src/mangomas/agents/chat.py"


class _StubBackend:
    def __init__(self, name: str, *, available: bool) -> None:
        self.name = name
        self._available = available
        self.timeouts: list[float] = []

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
        del argv, cwd, env
        self.timeouts.append(timeout_seconds)
        return IsolationResult(returncode=0, stdout="", stderr="", backend=self.name)


def _staged(tmp_path: Path) -> tuple[Path, Path]:
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    stage_candidate_file(staging, _CANDIDATE_REL, "VALUE = 3\n")
    return staging, repo


def test_build_oracle_isolation_default_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``cfg`` must honour ``MANGOMAS_REPAIR__*``, not a fresh ``RepairSettings()``."""
    monkeypatch.setenv(_ISOLATION_ENV, "required")
    monkeypatch.setenv(_BACKEND_ENV, "docker")
    get_settings.cache_clear()
    with (
        isolation_registry.scoped("docker", lambda _cfg: _StubBackend("docker", available=False)),
        pytest.raises(ConfigError, match="no isolating backend is available"),
    ):
        build_oracle_isolation()


def test_run_repair_oracle_passes_the_configured_timeout_to_the_backend(tmp_path: Path) -> None:
    stub = _StubBackend("docker", available=True)
    staging, repo = _staged(tmp_path)
    with isolation_registry.scoped("docker", lambda _cfg: stub):
        outcome = run_repair_oracle(
            RepairSettings(
                oracle_isolation="required",
                oracle_backend="docker",
                oracle_timeout_seconds=_CONFIGURED_TIMEOUT_SECONDS,
            ),
            staging_root=staging,
            repo_root=repo,
            relative_path=_CANDIDATE_REL,
            allowed_paths={_CANDIDATE_REL},
            oracle_argv=["{python}", "-c", "pass"],
        )
    assert outcome.promoted is True
    assert stub.timeouts == [_CONFIGURED_TIMEOUT_SECONDS]


def test_run_repair_oracle_honours_the_timeout_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(_ISOLATION_ENV, "required")
    monkeypatch.setenv(_BACKEND_ENV, "docker")
    monkeypatch.setenv(_TIMEOUT_ENV, str(_ENV_TIMEOUT_SECONDS))
    get_settings.cache_clear()
    stub = _StubBackend("docker", available=True)
    staging, repo = _staged(tmp_path)
    with isolation_registry.scoped("docker", lambda _cfg: stub):
        run_repair_oracle(
            staging_root=staging,
            repo_root=repo,
            relative_path=_CANDIDATE_REL,
            allowed_paths={_CANDIDATE_REL},
            oracle_argv=["{python}", "-c", "pass"],
        )
    assert stub.timeouts == [_ENV_TIMEOUT_SECONDS]


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
