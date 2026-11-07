"""Regression + AQA guards for the 2026-11-06 origin-delta validation (spec-0042).

Branch ``sdlc/origin-delta-aqa-20261106``, cut from
``origin/feat/initial-release@6fbf689`` (after PR #111 and PR #115). Each guard
is hermetic (fakes, ``CliRunner``, in-process calls — no sockets) and was run
red against the pre-fix tree before its fix landed. The register, with root
causes and the mutation proof for each guard, is ``docs/testing/rca-20261106.md``.

  D-1  ``SQLiteRepository.__init__`` let ``ValueError`` from
       ``path_from_sqlite_url`` (including PR #115's new ``sqlite:///``
       rejection and malformed IPv6 netlocs) escape untranslated instead of
       raising ``ConfigError``, crashing every CLI command with a raw Python
       traceback (exit 1) instead of ``EXIT_CONFIG_ERROR`` (exit 2).
  D-2  ``SqliteResultsSink.__init__`` let ``ValueError`` from
       ``path_from_sqlite_url`` escape untranslated instead of raising
       ``ConfigError``, violating ``_build_sinks``'s ``MangomasError`` contract
       and crashing ``mangomas eval`` with a raw traceback (exit 1) instead of
       ``EXIT_CONFIG_ERROR`` (exit 2).
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mangomas.adapters.storage.sqlite import SQLiteRepository
from mangomas.cli.commands._eval_config import _build_sinks
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR
from mangomas.cli.main import app
from mangomas.errors import ConfigError
from mangomas.eval.sinks.sqlite_results import SqliteResultsSink

_RUNNER = CliRunner()

_INVALID_SQLITE_URLS: tuple[str, ...] = (
    "sqlite:///",
    "sqlite:///?mode=ro",
    "sqlite://",
    "sqlite://[::1",
)


# ── D-1: SQLiteRepository invalid URL -> ConfigError (CLI exit 2) ────────────


@pytest.mark.parametrize("bad_url", _INVALID_SQLITE_URLS)
def test_d1_sqlite_repository_translates_invalid_url_to_config_error(bad_url: str) -> None:
    """``SQLiteRepository`` must raise ``ConfigError``, not a raw ``ValueError``.

    PR #115 made ``path_from_sqlite_url("sqlite:///")`` raise ``ValueError`` so
    ``sqlite3.connect("")`` could no longer silently open a throwaway temporary
    database. Without translating that ``ValueError`` to ``ConfigError`` at the
    repository boundary, ``build_or_exit`` (which catches ``MangomasError``)
    lets it escape as an unhandled crash.
    """
    with pytest.raises(ConfigError, match="Invalid SQLite database URL"):
        SQLiteRepository(bad_url)


@pytest.mark.parametrize("bad_url", _INVALID_SQLITE_URLS)
def test_d1_cli_chat_with_invalid_sqlite_url_exits_config_error_without_traceback(
    bad_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mangomas chat`` with ``MANGOMAS_DB__URL=sqlite:///`` exits 2, never 1."""
    monkeypatch.setenv("MANGOMAS_DB__PROVIDER", "sqlite")
    monkeypatch.setenv("MANGOMAS_DB__URL", bad_url)
    result = _RUNNER.invoke(app, ["chat", "hello"])
    assert result.exit_code == EXIT_CONFIG_ERROR, (
        f"expected exit {EXIT_CONFIG_ERROR}, got {result.exit_code}; "
        f"output={result.output!r} exc={result.exception!r}"
    )
    assert "Configuration error:" in result.output


# ── D-2: SqliteResultsSink invalid db_path -> ConfigError (CLI exit 2) ───────


@pytest.mark.parametrize("bad_url", _INVALID_SQLITE_URLS)
def test_d2_sqlite_results_sink_translates_invalid_db_path_to_config_error(
    bad_url: str,
) -> None:
    """``SqliteResultsSink`` must raise ``ConfigError`` on an invalid SQLite URL."""
    with pytest.raises(ConfigError, match="db_path"):
        SqliteResultsSink(db_path=bad_url)


@pytest.mark.parametrize("bad_url", _INVALID_SQLITE_URLS)
def test_d2_cli_eval_with_invalid_sqlite_results_db_path_exits_config_error(
    bad_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mangomas eval`` with ``sqlite_results.db_path=sqlite:///`` exits 2, never 1."""
    monkeypatch.setenv("MANGOMAS_EVAL__SINKS", json.dumps(["sqlite_results"]))
    monkeypatch.setenv(
        "MANGOMAS_EVAL__SINK_OPTIONS",
        json.dumps({"sqlite_results": {"db_path": bad_url}}),
    )
    result = _RUNNER.invoke(app, ["eval", "-d", "nonexistent.jsonl", "-t", "echo"])
    assert result.exit_code == EXIT_CONFIG_ERROR, (
        f"expected exit {EXIT_CONFIG_ERROR}, got {result.exit_code}; "
        f"output={result.output!r} exc={result.exception!r}"
    )
    assert "Eval configuration error:" in result.output


def test_d2_build_sinks_preserves_valid_sqlite_results_url() -> None:
    """Positive boundary: a valid ``sqlite:///`` URL still builds the sink."""
    sinks = _build_sinks(
        ["sqlite_results"],
        {"sqlite_results": {"db_path": "sqlite:///valid_eval.db"}},
        None,
    )
    assert len(sinks) == 1
    assert isinstance(sinks[0], SqliteResultsSink)
