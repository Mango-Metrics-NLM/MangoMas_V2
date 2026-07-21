"""Tests for ``scripts/harness_stop_gate.py``."""

from __future__ import annotations

import io

import pytest

from mangomas.config import get_settings
from mangomas.errors import ConfigError
from tests import constants
from tests._script_loader import load_script_module

hook = load_script_module("harness_stop_gate.py")


# ── _read_stdin_payload ───────────────────────────────────────────────────────


def test_read_stdin_payload_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO('{"stop_hook_active": true}'))
    assert hook._read_stdin_payload() == {"stop_hook_active": True}


def test_read_stdin_payload_returns_empty_dict_on_blank_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(""))
    assert hook._read_stdin_payload() == {}


def test_read_stdin_payload_returns_empty_dict_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("not-json{"))
    assert hook._read_stdin_payload() == {}


def test_read_stdin_payload_returns_empty_dict_on_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("[1, 2, 3]"))
    assert hook._read_stdin_payload() == {}


# ── main() ────────────────────────────────────────────────────────────────────


def test_main_skips_gate_when_stop_hook_already_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook, "_read_stdin_payload", lambda: constants.STOP_HOOK_ACTIVE_PAYLOAD)
    called = False

    def _fail_if_called(*_args: object, **_kwargs: object) -> int:
        nonlocal called
        called = True
        return 1

    monkeypatch.setattr(hook, "_run_pytest", _fail_if_called)
    assert hook.main() == hook.EXIT_OK
    assert called is False


def test_main_advisory_mode_always_exit_ok_even_on_pytest_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook, "_read_stdin_payload", lambda: constants.STOP_HOOK_INACTIVE_PAYLOAD)
    monkeypatch.setattr(hook, "read_coverage_floor", lambda: 95)
    monkeypatch.setattr(hook, "_run_pytest", lambda floor: 1)  # noqa: ARG005 — fixed test double

    monkeypatch.setenv("MANGOMAS_HARNESS__STOP_GATE_MODE", "advisory")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()


def test_main_enforced_mode_exit_ok_on_pytest_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook, "_read_stdin_payload", lambda: constants.STOP_HOOK_INACTIVE_PAYLOAD)
    monkeypatch.setattr(hook, "read_coverage_floor", lambda: 95)
    monkeypatch.setattr(hook, "_run_pytest", lambda floor: 0)  # noqa: ARG005 — fixed test double

    monkeypatch.setenv("MANGOMAS_HARNESS__STOP_GATE_MODE", "enforced")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()


def test_main_enforced_mode_exit_block_on_pytest_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook, "_read_stdin_payload", lambda: constants.STOP_HOOK_INACTIVE_PAYLOAD)
    monkeypatch.setattr(hook, "read_coverage_floor", lambda: 95)
    monkeypatch.setattr(hook, "_run_pytest", lambda floor: 1)  # noqa: ARG005 — fixed test double

    monkeypatch.setenv("MANGOMAS_HARNESS__STOP_GATE_MODE", "enforced")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_BLOCK
    finally:
        get_settings.cache_clear()


def test_main_degrades_to_exit_ok_when_floor_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook, "_read_stdin_payload", lambda: constants.STOP_HOOK_INACTIVE_PAYLOAD)

    def _raise_config_error() -> int:
        raise ConfigError("no pyproject.toml")

    monkeypatch.setattr(hook, "read_coverage_floor", _raise_config_error)
    monkeypatch.setenv("MANGOMAS_HARNESS__STOP_GATE_MODE", "enforced")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()


def test_main_degrades_to_exit_ok_when_pytest_invocation_raises_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook, "_read_stdin_payload", lambda: constants.STOP_HOOK_INACTIVE_PAYLOAD)
    monkeypatch.setattr(hook, "read_coverage_floor", lambda: 95)

    def _raise_oserror(_floor: int) -> int:
        raise OSError("python interpreter not found")

    monkeypatch.setattr(hook, "_run_pytest", _raise_oserror)
    monkeypatch.setenv("MANGOMAS_HARNESS__STOP_GATE_MODE", "enforced")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()
