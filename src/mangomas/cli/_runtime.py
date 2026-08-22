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

    Failures here never abort a command: observability setup must not be the
    reason a CLI invocation dies.
    """
    cfg = get_settings()
    try:
        configure_telemetry(
            log_level=VERBOSE_LOG_LEVEL if verbose else cfg.log_level,
            log_format=cfg.log.format,
            exporter=cfg.telemetry.exporter,
        )
    except Exception:  # pragma: no cover -- never fail a command over logging setup
        logging.basicConfig(level=VERBOSE_LOG_LEVEL if verbose else cfg.log_level)
        logging.getLogger(__name__).warning(
            "Telemetry not configured; falling back to basicConfig",
            exc_info=True,
        )
        return
    if verbose:
        # configure_telemetry is idempotent: if something already configured
        # telemetry at INFO (an earlier command in-process, or a lazy
        # get_tracer), the call above returned without touching the level.
        # Raise it explicitly so --verbose is honoured either way.
        logging.getLogger().setLevel(VERBOSE_LOG_LEVEL)
