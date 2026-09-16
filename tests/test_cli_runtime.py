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


def test_non_verbose_level_wins_over_a_latched_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A already-configured telemetry singleton must not pin the CLI's level.

    `configure_telemetry` is idempotent, so when *anything* configured it first
    the call inside `configure_cli_logging` returns having touched nothing.
    The level is re-applied unconditionally afterwards precisely for that case.
    Before the fix only the `verbose=True` branch re-applied it, so a plain run
    inherited whatever level the earlier caller had latched — here DEBUG, which
    would leave a non-verbose CLI invocation spewing debug output.

    Mutation proof: put the final `setLevel` back under `if verbose:` and this
    test fails with DEBUG != WARNING.
    """
    import logging  # noqa: PLC0415

    from mangomas.config import get_settings  # noqa: PLC0415
    from mangomas.telemetry import configure_telemetry  # noqa: PLC0415

    _reset_telemetry_state()
    configure_telemetry(log_level="DEBUG")  # the latch, e.g. a lazy get_tracer
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG

    monkeypatch.setenv("MANGOMAS_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()
    _runtime.configure_cli_logging(verbose=False)

    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_cli_import_chain_leaves_log_format_configurable() -> None:
    """End-to-end: `MANGOMAS_LOG__FORMAT` still takes effect after the CLI imports.

    Unlike the log *level*, the format is baked into the handler that the first
    `configure_telemetry` call installs — it cannot be re-applied afterwards.
    So this only holds while nothing in the `mangomas.cli.main` import chain
    configures telemetry first. It did: four modules bound
    `mangomas.telemetry.get_tracer` at module scope, and the CLI imported all
    of them, so every `mangomas ...` run was pinned to the default `text`
    format regardless of settings.

    Run in a fresh interpreter: pytest has long since configured telemetry
    in-process, which would mask the regression entirely.
    """
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    code = (
        "import mangomas.cli.main;"  # the full CLI import chain, first
        "import logging;"
        "from mangomas.cli._runtime import configure_cli_logging;"
        "configure_cli_logging();"
        "logging.getLogger('probe').warning('hello')"
    )
    env = {**os.environ, "MANGOMAS_LOG__FORMAT": "json", "MANGOMAS_LOG_LEVEL": "INFO"}
    result = subprocess.run(  # noqa: S603 -- trusted: fixed code string + sys.executable
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    line = result.stderr.strip().splitlines()[-1]
    record = _json.loads(line)  # a `text`-formatted line is not JSON at all
    assert record["message"] == "hello"
    assert record["severity"] == "WARNING"


def test_configure_cli_logging_is_idempotent() -> None:
    """Two commands in one process must not stack handlers."""
    import logging  # noqa: PLC0415

    _reset_telemetry_state()
    _runtime.configure_cli_logging(verbose=False)
    first = list(logging.getLogger().handlers)
    _runtime.configure_cli_logging(verbose=False)
    assert logging.getLogger().handlers == first


def test_telemetry_failure_degrades_instead_of_killing_the_command(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken exporter must not be the reason a CLI invocation dies.

    Previously this branch carried `# pragma: no cover`. It is a real
    behavioural fallback, not a race arm or an optional-extra import, so the
    repo's own coverage-audit guidance says test it rather than exclude it.
    """
    import logging  # noqa: PLC0415

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("exporter unreachable")

    _reset_telemetry_state()
    monkeypatch.setattr(_runtime, "configure_telemetry", _boom)

    with caplog.at_level(logging.WARNING, logger="mangomas.cli._runtime"):
        _runtime.configure_cli_logging(verbose=True)

    assert "falling back to basicConfig" in caplog.text


def test_metrics_failure_degrades_instead_of_killing_the_command(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken *metric* exporter is no more fatal than a broken span exporter.

    This is its own guard rather than an arm of the telemetry one because the
    two calls are guarded separately on purpose: the GCP trace and monitoring
    exporters ship as separate distributions, so `exporter=gcp` with only
    `opentelemetry-exporter-gcp-trace` installed succeeds at
    `configure_telemetry` and fails at `configure_metrics`. Folding it into the
    telemetry `except` would take the command's re-applied log level with it —
    which is what the second assertion pins.

    `configure_telemetry` is stubbed out, not left real: it calls
    `logging.basicConfig(force=True)`, which removes pytest's own root handlers
    and leaves `caplog.text` empty for the rest of the test (see the note on
    `tests/conftest.py::cli_logging_calls`).
    """
    import logging  # noqa: PLC0415

    from mangomas.config import get_settings  # noqa: PLC0415

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("metric exporter unreachable")

    monkeypatch.setattr(_runtime, "configure_telemetry", lambda **_kw: None)
    monkeypatch.setattr(_runtime, "configure_metrics", _boom)
    # ERROR, not WARNING: caplog already forces the root logger to WARNING, so
    # asserting WARNING would hold whether or not the level was re-applied.
    monkeypatch.setenv("MANGOMAS_LOG_LEVEL", "ERROR")
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="mangomas.cli._runtime"):
        _runtime.configure_cli_logging(verbose=False)

    assert "Metrics not configured" in caplog.text
    assert logging.getLogger().getEffectiveLevel() == logging.ERROR


def test_metrics_bootstrap_logs_its_outcome_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The operator-facing answer to "did this run install metrics?".

    Emitted on both directions of the flag, so a default-off run says so
    explicitly rather than logging nothing and leaving the question open — the
    state that made the missing `configure_metrics` call invisible for as long
    as it was.
    """
    import logging  # noqa: PLC0415

    from mangomas.config import get_settings  # noqa: PLC0415

    monkeypatch.setattr(_runtime, "configure_telemetry", lambda **_kw: None)
    monkeypatch.delenv("MANGOMAS_TELEMETRY__METRICS_ENABLED", raising=False)
    get_settings.cache_clear()

    with caplog.at_level(logging.DEBUG, logger="mangomas.cli._runtime"):
        _runtime.configure_cli_logging(verbose=True)

    records = [r for r in caplog.records if getattr(r, "event", None) == "cli_metrics_bootstrap"]
    assert records, "no cli_metrics_bootstrap record emitted"
    assert records[-1].metrics_enabled is False
    assert records[-1].exporter == get_settings().telemetry.exporter
    # `%s` lazy formatting, not an f-string: the record still carries its args.
    assert "enabled=False" in records[-1].getMessage()


def test_config_error_is_not_swallowed_as_a_logging_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid config must fail the command, not be masked by the guard.

    `get_settings()` is deliberately outside the try: a config error is a real
    error with its own exit code, and reporting it as "telemetry not
    configured" would send the operator hunting in the wrong place.
    """

    def _bad_settings() -> object:
        raise ValueError("invalid MANGOMAS_ setting")

    _reset_telemetry_state()
    monkeypatch.setattr(_runtime, "get_settings", _bad_settings)

    with pytest.raises(ValueError, match="invalid MANGOMAS_ setting"):
        _runtime.configure_cli_logging(verbose=False)
