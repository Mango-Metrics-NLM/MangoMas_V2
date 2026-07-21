"""Tests for ``scripts/harness_config_audit.py``."""

from __future__ import annotations

import io

import pytest

from mangomas.config import get_settings
from tests import constants
from tests._script_loader import load_script_module

hook = load_script_module("harness_config_audit.py")


# ── _read_stdin_payload ───────────────────────────────────────────────────────


def test_read_stdin_payload_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO('{"source": "project_settings"}'))
    assert hook._read_stdin_payload() == {"source": "project_settings"}


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


# ── _extract_source ───────────────────────────────────────────────────────────


def test_extract_source_finds_primary_key() -> None:
    assert hook._extract_source({"source": "project_settings"}) == "project_settings"


def test_extract_source_finds_fallback_key() -> None:
    assert hook._extract_source({"config_source": "local_settings"}) == "local_settings"


def test_extract_source_returns_none_when_absent() -> None:
    assert hook._extract_source({"unrelated": "value"}) is None


def test_extract_source_returns_none_when_value_not_string() -> None:
    assert hook._extract_source({"source": 123}) is None


# ── main() ────────────────────────────────────────────────────────────────────


def test_main_off_mode_always_exit_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hook,
        "_read_stdin_payload",
        lambda: {"source": constants.CONFIG_CHANGE_PROJECT_SETTINGS_SOURCE},
    )
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "off")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()


def test_main_audit_mode_exit_ok_for_governed_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hook,
        "_read_stdin_payload",
        lambda: {"source": constants.CONFIG_CHANGE_PROJECT_SETTINGS_SOURCE},
    )
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "audit")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()


def test_main_block_mode_exit_block_for_governed_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hook,
        "_read_stdin_payload",
        lambda: {"source": constants.CONFIG_CHANGE_LOCAL_SETTINGS_SOURCE},
    )
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "block")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_BLOCK
    finally:
        get_settings.cache_clear()


def test_main_block_mode_exit_ok_for_policy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``policy_settings`` can never be blocked by a hook, regardless of mode."""
    monkeypatch.setattr(
        hook,
        "_read_stdin_payload",
        lambda: {"source": constants.CONFIG_CHANGE_POLICY_SETTINGS_SOURCE},
    )
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "block")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()


def test_main_block_mode_exit_ok_when_source_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognized/missing source must fail open, never escalate to a block."""
    monkeypatch.setattr(hook, "_read_stdin_payload", dict)
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "block")
    get_settings.cache_clear()
    try:
        assert hook.main() == hook.EXIT_OK
    finally:
        get_settings.cache_clear()
