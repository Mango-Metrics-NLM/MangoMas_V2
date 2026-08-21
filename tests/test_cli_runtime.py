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
