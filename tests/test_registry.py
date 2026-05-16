"""Tests for the generic Registry class."""

from __future__ import annotations

import logging
import threading

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


# ── Thread-safety ────────────────────────────────────────────────────────────


def test_concurrent_register_and_get_does_not_corrupt_store() -> None:
    """Many threads writing + reading should never observe a torn state."""
    reg: Registry[int] = Registry("test")
    iterations = 200
    thread_count = 8
    errors: list[BaseException] = []

    def _writer(worker_id: int) -> None:
        try:
            for i in range(iterations):
                reg.register(f"w{worker_id}_k{i}", worker_id * 1000 + i)
        except BaseException as exc:
            errors.append(exc)

    def _reader(worker_id: int) -> None:
        try:
            for i in range(iterations):
                # Either the key exists (and value is correct) or it raises
                # UnknownProvider — never a half-set torn state.
                try:
                    value = reg.get(f"w{worker_id}_k{i}")
                    assert value == worker_id * 1000 + i
                except UnknownProvider:
                    pass
        except BaseException as exc:
            errors.append(exc)

    workers: list[threading.Thread] = []
    for wid in range(thread_count):
        workers.append(threading.Thread(target=_writer, args=(wid,)))
        workers.append(threading.Thread(target=_reader, args=(wid,)))

    for t in workers:
        t.start()
    for t in workers:
        t.join()

    assert not errors, f"thread-safety violation: {errors[:3]}"
    # After all writers finish, every key must be readable with the correct value.
    for wid in range(thread_count):
        for i in range(iterations):
            assert reg.get(f"w{wid}_k{i}") == wid * 1000 + i


def test_scoped_under_concurrent_load_restores_prior_value() -> None:
    """Nested `scoped()` from many threads must each restore their prior on exit."""
    reg: Registry[str] = Registry("test")
    reg.register("shared", "original")
    iterations = 50
    thread_count = 6
    errors: list[BaseException] = []

    def _worker(worker_id: int) -> None:
        try:
            for i in range(iterations):
                with reg.scoped("shared", f"w{worker_id}_v{i}"):
                    # Inside the block, we observe SOME value (not necessarily our own,
                    # since other threads have also entered scoped() — but never a half-set
                    # torn state and never KeyError).
                    _ = reg.get("shared")
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=_worker, args=(wid,)) for wid in range(thread_count)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()

    assert not errors, f"scoped() thread-safety violation: {errors[:3]}"
    # All `scoped()` blocks must have restored the original by exit.
    assert reg.get("shared") == "original"
