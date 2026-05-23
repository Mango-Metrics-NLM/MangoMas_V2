"""Tests for ``scripts/harness_session_start.py``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests import constants
from tests._script_loader import load_script_module

hook = load_script_module("harness_session_start.py")


def test_check_venv_returns_true_when_present(tmp_path: Path) -> None:
    (tmp_path / hook.VENV_DIRECTORY).mkdir()
    assert hook._check_venv(tmp_path) is True  # noqa: SLF001


def test_check_venv_returns_false_when_absent(tmp_path: Path) -> None:
    assert hook._check_venv(tmp_path) is False  # noqa: SLF001


async def test_probe_lmstudio_returns_false_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreachable LM Studio is gracefully degraded to ``False``."""

    class _RaisingClient:
        async def __aenter__(self) -> _RaisingClient:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def get(self, _url: str) -> object:
            raise httpx.ConnectError("connection refused")

    def fake_ctor(*_args: object, **_kwargs: object) -> _RaisingClient:
        return _RaisingClient()

    monkeypatch.setattr(hook.httpx, "AsyncClient", fake_ctor)
    result = await hook._probe_lmstudio(constants.DEFAULT_LLM_BASE_URL)  # noqa: SLF001
    assert result is False


async def test_probe_lmstudio_returns_true_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _OkResponse:
        def raise_for_status(self) -> None:
            return None

    class _OkClient:
        async def __aenter__(self) -> _OkClient:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def get(self, _url: str) -> _OkResponse:
            return _OkResponse()

    def fake_ctor(*_args: object, **_kwargs: object) -> _OkClient:
        return _OkClient()

    monkeypatch.setattr(hook.httpx, "AsyncClient", fake_ctor)
    assert await hook._probe_lmstudio(constants.DEFAULT_LLM_BASE_URL) is True  # noqa: SLF001


async def _make_false_coroutine(*_args: object, **_kwargs: object) -> bool:
    return False


def test_main_returns_exit_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` always returns EXIT_OK — hooks must never fail a session."""
    monkeypatch.setattr(hook, "_probe_lmstudio", _make_false_coroutine)
    assert hook.main() == hook.EXIT_OK
