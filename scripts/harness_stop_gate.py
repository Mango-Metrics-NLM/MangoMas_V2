"""Claude Code ``Stop`` hook for Mango-Mas V2.

Replaces the previous inline ``python -m pytest --cov=mangomas
--cov-fail-under=85 -q`` ``Stop``-hook command (see ADR-0011). Reads the
coverage floor from ``pyproject.toml`` at runtime via
:func:`mangomas.harness.coverage.read_coverage_floor` instead of hardcoding a
number that can drift from it (the historical 85 vs. ``pyproject.toml``'s 95
vs. ``ci.yml``'s 90), and checks ``stop_hook_active`` so this hook never
risks looping the session through Claude Code's 8-consecutive-block
override.

Modes (``MANGOMAS_HARNESS__STOP_GATE_MODE``, default ``advisory``):

``advisory``
    Always exits ``EXIT_OK`` — matches today's exact behavior. The correct
    coverage floor is now checked (any failure is visible in the transcript),
    but the Stop is never blocked.
``enforced``
    Exits ``EXIT_BLOCK`` when the coverage run fails.

Exit codes
----------
``EXIT_OK = 0``
    The Stop is allowed to proceed.
``EXIT_BLOCK = 2``
    ``enforced`` mode and the coverage run failed.

Run::

    python scripts/harness_stop_gate.py
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Final

from mangomas.config import get_settings
from mangomas.errors import ConfigError
from mangomas.harness.coverage import (
    build_pytest_args,
    read_coverage_floor,
    resolve_stop_gate_exit_code,
    should_skip_active_stop_hook,
)
from mangomas.telemetry import configure_telemetry

logger = logging.getLogger(__name__)

EXIT_OK: Final[int] = 0
EXIT_BLOCK: Final[int] = 2


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
    empty stdin degrades to "not active, use defaults" rather than raising.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Stop hook received non-JSON stdin; ignoring")
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _run_pytest(floor: int) -> int:
    """Run pytest at *floor* in a subprocess and return its exit code."""
    args = build_pytest_args(floor)
    result = subprocess.run(
        [sys.executable, *args],
        check=False,
    )
    return result.returncode


def main() -> int:
    """Run the Stop-hook coverage gate; return ``EXIT_OK`` or ``EXIT_BLOCK``."""
    try:
        _configure_logging()
    except Exception as exc:  # never fail the session over telemetry setup
        logging.basicConfig(level=logging.INFO)
        logger.warning(
            "Harness logging not configured; falling back to basicConfig",
            extra={"error": str(exc)},
        )

    payload = _read_stdin_payload()
    if should_skip_active_stop_hook(payload):
        logger.info("Stop hook already active; skipping to avoid the 8-block override")
        return EXIT_OK

    mode = get_settings().harness.stop_gate_mode

    try:
        floor = read_coverage_floor()
    except ConfigError as exc:
        logger.warning("Coverage floor unreadable; skipping gate", extra={"error": str(exc)})
        return EXIT_OK

    try:
        returncode = _run_pytest(floor)
    except OSError as exc:
        logger.warning("pytest invocation failed; skipping gate", extra={"error": str(exc)})
        return EXIT_OK

    return resolve_stop_gate_exit_code(mode, returncode)


if __name__ == "__main__":
    sys.exit(main())
