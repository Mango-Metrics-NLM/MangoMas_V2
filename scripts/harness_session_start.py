"""Claude Code SessionStart hook for Mango-Mas V2.

Performs two light-weight checks at session start:

1. Confirm a Python virtualenv is present so subsequent hooks
   (``ruff``/``mypy``/``pytest``) have the toolchain available.  Absent
   ``.venv`` is **warned**, not failed — many contributors install dev
   tools into the system interpreter.
2. Confirm the configured LM Studio endpoint is reachable so integration
   tests will run.  Unreachable LM Studio is **warned**, not failed —
   most sessions are pure unit work.

The hook emits structured logs via the shared
:func:`mangomas.telemetry.get_tracer` namespace so operators see hook
activity in the same trace tree as orchestrator activity.  Exit code is
always ``0`` — the hook never fails a session.

That contract has to hold on the exact machine the warnings exist for: a
fresh web session with no venv, where neither ``httpx`` nor ``mangomas`` is
importable.  Both imports are therefore guarded (the same degradation
posture as ``harness_config_audit.py``) — an absent dependency downgrades
the corresponding probe to a warning instead of dying at import time with
``ModuleNotFoundError`` / exit 1 (spec-0022 R3).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Final

import _logging

try:
    import httpx
except ImportError:  # bare web-session interpreter — degrade, never die
    httpx = None  # type: ignore[assignment]

try:
    from mangomas.config import get_settings
    from mangomas.telemetry import configure_telemetry
except ImportError:  # mangomas not installed — probes degrade to warnings
    _MANGOMAS_AVAILABLE = False
else:
    _MANGOMAS_AVAILABLE = True

logger = logging.getLogger(__name__)

VENV_DIRECTORY: Final[str] = ".venv"
LMSTUDIO_PROBE_TIMEOUT_SECONDS: Final[float] = 2.0
PROBE_PATH: Final[str] = "/models"
EXIT_OK: Final[int] = 0


def _check_venv(root: Path) -> bool:
    """Return ``True`` when a ``.venv`` directory exists under *root*."""
    candidate = root / VENV_DIRECTORY
    exists = candidate.is_dir()
    if exists:
        logger.info("Virtualenv present", extra={"path": str(candidate)})
    else:
        logger.warning(
            "Virtualenv not found — system interpreter assumed",
            extra={"expected_path": str(candidate)},
        )
    return exists


async def _probe_lmstudio(
    base_url: str,
    *,
    timeout_seconds: float = LMSTUDIO_PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Return ``True`` when LM Studio responds to a quick GET ``/models``."""
    url = base_url.rstrip("/") + PROBE_PATH
    if httpx is None:
        logger.warning(
            "LM Studio probe unavailable — httpx not installed",
            extra={"url": url},
        )
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning(
            "LM Studio probe failed — integration tests will skip",
            extra={"url": url, "error": str(exc)},
        )
        return False

    logger.info("LM Studio reachable", extra={"url": url})
    return True


def _configure_logging() -> None:
    _logging.configure_harness_telemetry_logging(
        logger,
        available=_MANGOMAS_AVAILABLE,
        fallback_message="Harness logging not configured; falling back to basicConfig",
        include_exc_info=False,
        extra_on_error=True,
        get_settings_fn=get_settings if _MANGOMAS_AVAILABLE else None,
        configure_telemetry_fn=configure_telemetry if _MANGOMAS_AVAILABLE else None,
    )


def main() -> int:
    """Run the session-start checks; return ``EXIT_OK`` unconditionally."""
    _configure_logging()

    repo_root = Path(__file__).resolve().parents[1]
    _check_venv(repo_root)

    if not _MANGOMAS_AVAILABLE:
        logger.warning("Probe skipped — mangomas not installed")
        return EXIT_OK

    try:
        cfg = get_settings()
        if cfg.llm.provider == "lmstudio":
            asyncio.run(
                _probe_lmstudio(
                    cfg.llm.base_url,
                    timeout_seconds=cfg.api.ready_timeout_seconds,
                )
            )
    except Exception as exc:  # never fail the session over an unreachable upstream
        logger.warning("Probe skipped", extra={"error": str(exc)})

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
