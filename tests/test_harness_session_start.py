"""Tests for ``scripts/harness_session_start.py``."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from tests import constants
from tests._script_loader import load_script_module

hook = load_script_module("harness_session_start.py")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "harness_session_start.py"


def test_check_venv_returns_true_when_present(tmp_path: Path) -> None:
    (tmp_path / hook.VENV_DIRECTORY).mkdir()
    assert hook._check_venv(tmp_path) is True


def test_check_venv_returns_false_when_absent(tmp_path: Path) -> None:
    assert hook._check_venv(tmp_path) is False


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
    result = await hook._probe_lmstudio(constants.DEFAULT_LLM_BASE_URL)
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
    assert await hook._probe_lmstudio(constants.DEFAULT_LLM_BASE_URL) is True


async def _make_false_coroutine(*_args: object, **_kwargs: object) -> bool:
    return False


def test_main_returns_exit_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` always returns EXIT_OK — hooks must never fail a session."""
    monkeypatch.setattr(hook, "_probe_lmstudio", _make_false_coroutine)
    assert hook.main() == hook.EXIT_OK


async def test_probe_lmstudio_returns_false_when_httpx_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent httpx degrades the probe to a warning, not an ImportError."""
    monkeypatch.setattr(hook, "httpx", None)
    assert await hook._probe_lmstudio(constants.DEFAULT_LLM_BASE_URL) is False


def test_main_returns_exit_ok_when_mangomas_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-process half of the bare-interpreter contract (spec-0022 R3)."""
    monkeypatch.setattr(hook, "_MANGOMAS_AVAILABLE", False)
    assert hook.main() == hook.EXIT_OK


def test_bare_interpreter_exits_ok(tmp_path: Path) -> None:
    """The script must exit 0 with httpx AND mangomas unimportable.

    This is the exact machine the hook's warnings exist for — a fresh web
    session with no venv. Before spec-0022 R3 the module-scope imports died
    there with ``ModuleNotFoundError`` / exit 1, making the documented
    "always returns EXIT_OK" contract unmeetable. Stub modules that raise
    ImportError shadow the real installs via PYTHONPATH.
    """
    (tmp_path / "httpx.py").write_text(
        'raise ImportError("stubbed absent for the bare-interpreter test")\n',
        encoding="utf-8",
    )
    (tmp_path / "mangomas").mkdir()
    (tmp_path / "mangomas" / "__init__.py").write_text(
        'raise ImportError("stubbed absent for the bare-interpreter test")\n',
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT)],
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
