"""Tests for ``mangomas.harness.coverage``."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.errors import ConfigError
from mangomas.harness import coverage
from tests import constants

# ── read_coverage_floor ──────────────────────────────────────────────────────


def test_read_coverage_floor_happy_path(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(constants.FAKE_PYPROJECT_TOML, encoding="utf-8")
    assert coverage.read_coverage_floor(pyproject) == constants.FAKE_PYPROJECT_TOML_COVERAGE_FLOOR


def test_read_coverage_floor_missing_file_raises_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(ConfigError, match="cannot read"):
        coverage.read_coverage_floor(missing)


def test_read_coverage_floor_malformed_toml_raises_config_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(constants.FAKE_PYPROJECT_TOML_MALFORMED, encoding="utf-8")
    with pytest.raises(ConfigError, match="malformed TOML"):
        coverage.read_coverage_floor(pyproject)


def test_read_coverage_floor_missing_addopts_key_raises_config_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(constants.FAKE_PYPROJECT_TOML_MISSING_ADDOPTS, encoding="utf-8")
    with pytest.raises(ConfigError, match="addopts"):
        coverage.read_coverage_floor(pyproject)


def test_read_coverage_floor_missing_token_raises_config_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(constants.FAKE_PYPROJECT_TOML_MISSING_TOKEN, encoding="utf-8")
    with pytest.raises(ConfigError, match="cov-fail-under"):
        coverage.read_coverage_floor(pyproject)


def test_read_coverage_floor_defaults_to_repo_pyproject() -> None:
    """Omitting the path argument reads ``./pyproject.toml`` (the real gate)."""
    assert coverage.read_coverage_floor() > 0


# ── build_pytest_args ─────────────────────────────────────────────────────────


def test_build_pytest_args_includes_floor() -> None:
    args = coverage.build_pytest_args(95)
    assert args[-1] == "--cov-fail-under=95"
    assert "--cov=mangomas" in args


# ── should_skip_active_stop_hook ──────────────────────────────────────────────


def test_should_skip_active_stop_hook_true_when_active() -> None:
    assert coverage.should_skip_active_stop_hook(constants.STOP_HOOK_ACTIVE_PAYLOAD) is True


def test_should_skip_active_stop_hook_false_when_inactive() -> None:
    assert coverage.should_skip_active_stop_hook(constants.STOP_HOOK_INACTIVE_PAYLOAD) is False


def test_should_skip_active_stop_hook_false_when_key_absent() -> None:
    assert coverage.should_skip_active_stop_hook({}) is False


# ── resolve_stop_gate_exit_code ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "pytest_returncode", "expected_exit_code"),
    [
        ("advisory", 0, 0),
        ("advisory", 1, 0),
        ("enforced", 0, 0),
        ("enforced", 1, 2),
    ],
)
def test_resolve_stop_gate_exit_code(
    mode: coverage.StopGateMode, pytest_returncode: int, expected_exit_code: int
) -> None:
    assert coverage.resolve_stop_gate_exit_code(mode, pytest_returncode) == expected_exit_code
