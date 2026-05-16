"""Tests for the generic Registry class."""

from __future__ import annotations

import logging

import pytest

from mangomas.errors import ConfigError, UnknownProvider
from mangomas.registry import Registry


def test_register_and_get_returns_value() -> None:
    reg: Registry[str] = Registry("test")
    reg.register("foo", "bar")
    assert reg.get("foo") == "bar"


def test_get_unknown_raises_unknown_provider() -> None:
    reg: Registry[str] = Registry("test")
    reg.register("a", "val-a")
    with pytest.raises(UnknownProvider) as exc_info:
        reg.get("missing")
    exc = exc_info.value
    assert exc.name == "missing"
    assert "a" in exc.available


def test_unknown_provider_is_config_error() -> None:
    reg: Registry[int] = Registry("test")
    with pytest.raises(ConfigError):
        reg.get("nope")


def test_available_returns_sorted_names() -> None:
    reg: Registry[int] = Registry("test")
    reg.register("b", 2)
    reg.register("a", 1)
    reg.register("c", 3)
    assert reg.available() == ["a", "b", "c"]


def test_last_registration_wins() -> None:
    reg: Registry[str] = Registry("test")
    reg.register("key", "first")
    reg.register("key", "second")
    assert reg.get("key") == "second"


def test_registry_log_on_register(caplog: pytest.LogCaptureFixture) -> None:
    reg: Registry[str] = Registry("llm")
    with caplog.at_level(logging.DEBUG, logger="mangomas.registry"):
        reg.register("myprovider", "val")
    assert "myprovider" in caplog.text


def test_registry_log_on_unknown_get(caplog: pytest.LogCaptureFixture) -> None:
    reg: Registry[str] = Registry("storage")
    with caplog.at_level(logging.ERROR, logger="mangomas.registry"), pytest.raises(UnknownProvider):
        reg.get("ghost")
    assert "ghost" in caplog.text


# ── scoped() context manager ─────────────────────────────────────────────────


def test_scoped_overrides_existing_value_then_restores() -> None:
    reg: Registry[str] = Registry("test")
    reg.register("k", "original")
    with reg.scoped("k", "swapped"):
        assert reg.get("k") == "swapped"
    assert reg.get("k") == "original"


def test_scoped_inserts_then_removes_when_no_prior_value() -> None:
    reg: Registry[str] = Registry("test")
    with reg.scoped("new", "temp"):
        assert reg.get("new") == "temp"
    with pytest.raises(UnknownProvider):
        reg.get("new")


def test_scoped_restores_prior_value_when_block_raises() -> None:
    reg: Registry[str] = Registry("test")
    reg.register("k", "original")
    with pytest.raises(RuntimeError), reg.scoped("k", "swapped"):
        raise RuntimeError("boom")
    assert reg.get("k") == "original"


def test_scoped_removes_value_when_block_raises_and_no_prior() -> None:
    reg: Registry[str] = Registry("test")
    with pytest.raises(RuntimeError), reg.scoped("k", "temp"):
        raise RuntimeError("boom")
    with pytest.raises(UnknownProvider):
        reg.get("k")
