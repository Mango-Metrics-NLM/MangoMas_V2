"""Process- and orchestrator-level seam for the CLI.

The base layer: imports nothing else under `mangomas.cli`, so every command
module can depend on it without a cycle.

`_build` and `_close_orchestrator` are the **patch seam** the CLI test suites
replace. Command modules must call them through this module object
(`_runtime._build()`), never via `from ._runtime import _build` — a bound name
would need a separate patch target per command module, and the one that got
missed would fail silently. `tests/_seam_guards.forbid_real_orchestrator`
enforces that a missed patch is loud rather than quiet.

The Windows stdout reconfiguration lives here rather than in the facade because
it is a process-level side effect that must run before any `typer.echo`, and
this module is imported by every command path.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from mangomas.composition import build_orchestrator
from mangomas.config import get_settings
from mangomas.telemetry import configure_telemetry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator

# Windows default console codec is cp1252; LLM replies routinely contain
# em-dashes, smart quotes, etc. that cp1252 cannot encode, which crashes
# typer.echo. Reconfigure to UTF-8 with replacement so output never crashes.
if sys.platform == "win32":  # pragma: no cover — platform-gated
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _build() -> Orchestrator:
    """Construct the orchestrator with full settings + adapter wiring."""
    return build_orchestrator()


async def _close_orchestrator(orch: Orchestrator) -> None:
    """Release adapter resources cleanly via :meth:`Orchestrator.aclose`.

    Retained as a thin wrapper so existing tests can monkeypatch the CLI's
    close path without reaching into core. The real teardown logic lives on
    :class:`~mangomas.core.Orchestrator` so every entry point (CLI, FastAPI
    lifespan, demo scripts) shares one tested code path.
    """
    await orch.aclose()


# Level applied when a command is run with ``--verbose``/``-v``. Named rather
# than inlined so every command shares one definition of "verbose".
VERBOSE_LOG_LEVEL: str = "DEBUG"


def configure_cli_logging(*, verbose: bool = False) -> None:
    """Bootstrap logging + tracing for a CLI command. Idempotent.

    Commands used to call ``logging.basicConfig(level=DEBUG)`` directly, which
    was silently undone mid-run: the first ``get_tracer()`` deep in the
    dispatch path lazily calls :func:`configure_telemetry` with its *default*
    ``log_level="INFO"`` and ``force=True``, replacing the root handler and
    resetting the level. Every ``logger.debug`` after that point vanished — so
    ``--verbose`` stopped working exactly where the interesting work happens.
    Routing through :func:`configure_telemetry` first also means a CLI run
    honours ``MANGOMAS_LOG__FORMAT``, ``MANGOMAS_LOG_LEVEL`` and
    ``MANGOMAS_TELEMETRY__EXPORTER`` instead of hard-coded defaults, and gets
    the ``TraceContextFilter``/``CorrelationFilter`` that carry ``trace_id``
    and ``correlation_id`` onto every record.

    A telemetry-setup failure degrades to ``basicConfig`` rather than killing
    the command — observability must not be the reason a CLI invocation dies.
    A *configuration* error is deliberately NOT swallowed: ``get_settings()``
    is called outside the guard, so invalid config still fails the command
    (``EXIT_CONFIG_ERROR``) instead of being masked as a logging problem.
    """
    cfg = get_settings()
    level = VERBOSE_LOG_LEVEL if verbose else cfg.log_level
    try:
        configure_telemetry(
            log_level=level,
            log_format=cfg.log.format,
            exporter=cfg.telemetry.exporter,
        )
    except Exception:
        logging.basicConfig(level=level)
        logging.getLogger(__name__).warning(
            "Telemetry not configured; falling back to basicConfig",
            exc_info=True,
        )
        return
    # configure_telemetry is idempotent: if anything already configured
    # telemetry (an earlier command in-process, a test, or a lazy get_tracer),
    # the call above returned without touching the level. Re-apply it
    # unconditionally so the resolved level wins either way — the non-verbose
    # path needs this as much as `--verbose` does, since a latched DEBUG would
    # otherwise leave a plain run spewing debug output, and a latched INFO
    # would swallow a configured `MANGOMAS_LOG_LEVEL=DEBUG`.
    #
    # Only the *level* is recoverable this way. Log format and span exporter
    # are baked into the handler/provider that the first caller installed, so
    # they are honoured only when this really is the first call — which is why
    # `tests/test_telemetry.py::test_no_module_configures_telemetry_at_import`
    # forbids module-level `mangomas.telemetry.get_tracer` bindings anywhere
    # under `src/`.
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.INFO))
