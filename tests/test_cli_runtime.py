"""Tests for `mangomas.cli._runtime` — the CLI's orchestrator seam.

`_build` and `_close_orchestrator` are the two functions every CLI suite
replaces, which is exactly why the real ones went untested: `_build`'s single
statement was the one line in the whole `cli` package no test executed, hidden
behind a package-aggregate coverage floor that the pre-decomposition
`cli/main.py` kept comfortably above 95%.

This file deliberately calls the **real** seam. `tests/_seam_guards` exists to
stop a *command* test from doing that by accident; here it is the subject, not
an accident, so the guard is correctly absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.cli import _runtime
from mangomas.core import Orchestrator


async def test_build_returns_a_real_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_build()` wires settings → adapters → orchestrator, offline.

    `chdir` first: the default `MANGOMAS_DB__URL` is the *relative*
    `sqlite:///./data/mangomas.db`, and `build_orchestrator` creates that file
    eagerly rather than on first query. Without this the test would silently
    write into the repository working tree — passing either way, which is the
    failure mode this whole commit sequence exists to remove.

    No network: the LM Studio adapter constructs its `httpx` client without
    connecting, so nothing here needs a live endpoint.
    """
    monkeypatch.chdir(tmp_path)

    orch = _runtime._build()
    try:
        assert isinstance(orch, Orchestrator)
        assert orch.list_agents(), "a default build must register the built-in agents"
    finally:
        await _runtime._close_orchestrator(orch)


async def test_build_is_not_a_singleton(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each call constructs its own orchestrator.

    Commands own their orchestrator for the length of one invocation and close
    it in a `finally`. If `_build` ever memoised, the first command to finish
    would close the adapters out from under every later one — and in a CLI
    process that runs a single command, nothing would ever notice.
    """
    monkeypatch.chdir(tmp_path)

    first = _runtime._build()
    second = _runtime._build()
    try:
        assert first is not second
    finally:
        await _runtime._close_orchestrator(first)
        await _runtime._close_orchestrator(second)


# ── configure_cli_logging (spec-0023 R1) ──────────────────────────────────────


def _reset_telemetry_state() -> None:
    """Drop the idempotency latch so each test configures from scratch."""
    from mangomas.telemetry import _state  # noqa: PLC0415 -- test-local reset

    _state.configured = False


def test_verbose_survives_a_lazy_get_tracer(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--verbose` must still emit DEBUG after the tracer bootstraps.

    The regression this pins: commands used to call
    `logging.basicConfig(level=DEBUG)` themselves, and the first `get_tracer()`
    deep in the dispatch path then lazily called `configure_telemetry()` with
    its default `log_level="INFO"` and `force=True` — replacing the root
    handler and resetting the level. Every `logger.debug` after that point was
    dropped, so `--verbose` went dead exactly where the interesting work
    happens. Mutation proof: revert `configure_cli_logging` to a bare
    `basicConfig(level=DEBUG)` and this test fails on the post-tracer assert.
    """
    import logging  # noqa: PLC0415 -- exercising real logging state

    from mangomas.telemetry import get_tracer  # noqa: PLC0415

    _reset_telemetry_state()
    monkeypatch.delenv("MANGOMAS_LOG_LEVEL", raising=False)

    _runtime.configure_cli_logging(verbose=True)
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG

    # The lazy bootstrap that used to clobber the level.
    get_tracer("mangomas.probe")

    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG


def test_non_verbose_honours_configured_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `--verbose` the level comes from Settings, not a hard-coded INFO."""
    import logging  # noqa: PLC0415

    from mangomas.config import get_settings  # noqa: PLC0415

    _reset_telemetry_state()
    monkeypatch.setenv("MANGOMAS_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()

    _runtime.configure_cli_logging(verbose=False)
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_configure_cli_logging_is_idempotent() -> None:
    """Two commands in one process must not stack handlers."""
    import logging  # noqa: PLC0415

    _reset_telemetry_state()
    _runtime.configure_cli_logging(verbose=False)
    first = list(logging.getLogger().handlers)
    _runtime.configure_cli_logging(verbose=False)
    assert logging.getLogger().handlers == first
