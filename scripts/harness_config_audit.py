"""Claude Code ``ConfigChange`` hook for Mango-Mas V2.

New in ADR-0011. ``ConfigChange`` fires only for Claude Code's own
configuration sources (``project_settings`` = ``.claude/settings.json``,
``local_settings`` = ``.claude/settings.local.json``, ``policy_settings``,
``skills``, ``user_settings``) — never for arbitrary project files such as
``pyproject.toml``. This repo's ``.claude/settings.json`` wiring matcher-scopes
this hook to ``project_settings|local_settings``, the two sources it owns.

The exact stdin JSON field naming the changed source is undocumented in the
public ``hooks.md`` schema at the time this was written; ``_extract_source``
tries a small set of candidate keys and fails open (returns ``None``, which
:func:`mangomas.harness.config_audit.evaluate_config_change` always resolves
to ``allow``) if none match. Confirm the real key on first live firing (or
against an updated ``hooks.md``) before relying on ``audit``/``block`` mode.

Modes (``MANGOMAS_HARNESS__CONFIG_AUDIT_MODE``, default ``off``):

``off``
    Always exits ``EXIT_OK`` — the hook is inert. This is today's exact
    behavior (no ``ConfigChange`` hook existed before ADR-0011).
``audit``
    Logs governed-source changes; always exits ``EXIT_OK``.
``block``
    Exits ``EXIT_BLOCK`` for governed sources (``project_settings`` /
    ``local_settings``); never for ``policy_settings`` (Claude Code cannot
    block those regardless of mode) or an unrecognized/missing source.

Exit codes
----------
``EXIT_OK = 0``
    The config change is allowed to proceed (or was only audited).
``EXIT_BLOCK = 2``
    ``block`` mode and the source resolved to a blockable governed change.

Run::

    python scripts/harness_config_audit.py
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Final

from mangomas.config import get_settings
from mangomas.harness.config_audit import evaluate_config_change
from mangomas.telemetry import configure_telemetry

logger = logging.getLogger(__name__)

EXIT_OK: Final[int] = 0
EXIT_BLOCK: Final[int] = 2

# Candidate stdin keys tried, in order, for the changed source. Not confirmed
# against a live payload — see module docstring and ADR-0011's known
# limitation.
_SOURCE_KEY_CANDIDATES: Final[tuple[str, ...]] = ("source", "config_source", "hook_event_source")


def _configure_logging() -> None:
    cfg = get_settings()
    configure_telemetry(
        service_name=cfg.harness.metrics_namespace,
        log_level=cfg.harness.hook_log_level,
        log_format=cfg.log.format,
        exporter=cfg.telemetry.exporter,
    )


def _read_stdin_payload() -> dict[str, object]:
    """Return the hook's stdin JSON, or ``{}`` on any parse failure.

    A hook must never crash a session over its own plumbing — malformed or
    empty stdin degrades to "no recognized source" rather than raising.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ConfigChange hook received non-JSON stdin; ignoring")
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _extract_source(payload: dict[str, object]) -> str | None:
    """Return the changed config source named in *payload*, or ``None``.

    The real stdin field name is unconfirmed (see module docstring); this
    tries each candidate key and logs the raw payload at DEBUG so the real
    key can be confirmed on first live firing.
    """
    for key in _SOURCE_KEY_CANDIDATES:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    logger.debug("ConfigChange payload had no recognized source key", extra={"payload": payload})
    return None


def main() -> int:
    """Evaluate a ``ConfigChange`` firing; return ``EXIT_OK`` or ``EXIT_BLOCK``."""
    try:
        _configure_logging()
    except Exception as exc:  # never fail the session over telemetry setup
        logging.basicConfig(level=logging.INFO)
        logger.warning(
            "Harness logging not configured; falling back to basicConfig",
            extra={"error": str(exc)},
        )

    payload = _read_stdin_payload()
    source = _extract_source(payload)
    mode = get_settings().harness.config_audit_mode

    decision = evaluate_config_change(source, mode)
    log_extra = {"source": decision.source, "reason": decision.reason}
    if decision.action == "block":
        logger.error("Config change blocked", extra=log_extra)
        return EXIT_BLOCK
    if decision.action == "audit":
        logger.warning("Config change audited", extra=log_extra)
        return EXIT_OK
    logger.info("Config change allowed", extra=log_extra)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
