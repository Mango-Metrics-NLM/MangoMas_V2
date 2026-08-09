"""Claude Code ``ConfigChange`` hook for Mango-Mas V2 (ADR-0021 / spec-0017).

Ported from ``origin/main``'s ``scripts/harness_config_audit.py`` (ADR-0011
there), adapted so the ``mangomas.config``/``mangomas.telemetry`` imports —
which pull in ``pydantic``/``opentelemetry`` — are deferred into function
bodies wrapped in ``except Exception``, matching this branch's stdin-hook
hardening (spec-0017 R3/R6): a hook must degrade gracefully, never crash a
session, even in an interpreter where ``mangomas`` was never installed.
``mangomas.harness.config_audit`` itself has no such dependency (pure
stdlib), so ``evaluate_config_change`` imports at module level safely.

``ConfigChange`` fires only for Claude Code's own configuration sources
(``project_settings`` = ``.claude/settings.json``, ``local_settings`` =
``.claude/settings.local.json``, ``policy_settings``, ``skills``,
``user_settings``) — never for arbitrary project files such as
``pyproject.toml``. This repo's ``.claude/settings.json`` wiring
matcher-scopes this hook to ``project_settings|local_settings``, the two
sources it owns.

The exact stdin JSON field naming the changed source is undocumented in the
public ``hooks.md`` schema at the time this was written; ``_extract_source``
tries a small set of candidate keys and fails open (returns ``None``, which
:func:`mangomas.harness.config_audit.evaluate_config_change` always resolves
to ``allow``) if none match.

Modes (``MANGOMAS_HARNESS__CONFIG_AUDIT_MODE``, default ``off``):

``off``
    Always exits ``EXIT_OK`` — the hook is inert. This is today's exact
    behavior (no ``ConfigChange`` hook existed before ADR-0021).
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

from mangomas.harness.config_audit import ConfigChangeMode, evaluate_config_change

logger = logging.getLogger(__name__)

EXIT_OK: Final[int] = 0
EXIT_BLOCK: Final[int] = 2

# Candidate stdin keys tried, in order, for the changed source. Not confirmed
# against a live payload — see module docstring.
_SOURCE_KEY_CANDIDATES: Final[tuple[str, ...]] = ("source", "config_source", "hook_event_source")

# Mirrors mangomas.config.DEFAULT_HARNESS_CONFIG_AUDIT_MODE — duplicated as a
# literal (not imported) so this fallback works even when mangomas.config
# itself cannot be imported (see _resolve_mode).
_FALLBACK_CONFIG_AUDIT_MODE: Final[ConfigChangeMode] = "off"


def _configure_logging() -> None:
    """Best-effort structured logging via the app's telemetry setup.

    Falls back to bare ``basicConfig`` on any failure (including
    ``mangomas.telemetry`` — and everything it needs — not being importable)
    since a hook must never crash a session over its own plumbing.
    """
    try:
        from mangomas.config import get_settings  # noqa: PLC0415
        from mangomas.telemetry import configure_telemetry  # noqa: PLC0415

        cfg = get_settings()
        configure_telemetry(
            service_name=cfg.harness.metrics_namespace,
            log_level=cfg.harness.hook_log_level,
            log_format=cfg.log.format,
            exporter=cfg.telemetry.exporter,
        )
    except Exception:
        logging.basicConfig(level=logging.INFO)


def _resolve_mode() -> ConfigChangeMode:
    """Return the configured ``config_audit_mode``, falling back to the
    documented default on any failure — including ``mangomas`` not being
    importable at all, not just a settings-validation error."""
    try:
        from mangomas.config import get_settings  # noqa: PLC0415

        return get_settings().harness.config_audit_mode
    except Exception:
        logger.warning(
            "Harness settings unavailable; falling back to default config_audit_mode",
            exc_info=True,
        )
        return _FALLBACK_CONFIG_AUDIT_MODE


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
    return payload if isinstance(payload, dict) else {}


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
    _configure_logging()
    payload = _read_stdin_payload()
    source = _extract_source(payload)
    mode = _resolve_mode()

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
