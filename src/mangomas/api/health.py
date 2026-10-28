"""Readiness probe helpers: check LLM + DB connectivity and report status."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Final

from mangomas.adapters.llm.base import PingableLLMClient
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.core.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# The probe routes, in one place: ``routes/system.py`` mounts them and the
# concurrency guard exempts them (``create_app``). Each pair is the canonical
# path plus its retained compatibility alias.
LIVENESS_PATHS: Final[tuple[str, str]] = ("/healthz", "/health")
READINESS_PATHS: Final[tuple[str, str]] = ("/readyz", "/ready")
PROBE_PATHS: Final[frozenset[str]] = frozenset((*LIVENESS_PATHS, *READINESS_PATHS))

_STATUS_OK = "ok"
_STATUS_ERROR = "error"
_STATUS_NOT_CONFIGURED = "not_configured"
_STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single readiness check.

    Parameters
    ----------
    name:
        Human-readable identifier (e.g. ``"llm"``, ``"db"``).
    status:
        One of ``"ok"``, ``"error"``, ``"not_configured"``, ``"unknown"``.
    detail:
        Optional free-text description (e.g. the error message).
    """

    name: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Return ``True`` when the check is non-failing."""
        return self.status in {_STATUS_OK, _STATUS_NOT_CONFIGURED, _STATUS_UNKNOWN}


@dataclass
class ReadinessReport:
    """Aggregated result of all readiness checks."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """``True`` when every check is non-failing."""
        return all(c.ok for c in self.checks)

    @property
    def http_status(self) -> int:
        """HTTP status code: 200 when ready, 503 when not."""
        return HTTPStatus.OK.value if self.ready else HTTPStatus.SERVICE_UNAVAILABLE.value

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict suitable for a JSONResponse body."""
        return {
            "ready": self.ready,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    **({"detail": c.detail} if c.detail else {}),
                }
                for c in self.checks
            ],
        }


async def _bounded(probe: Awaitable[Any], timeout_seconds: float | None) -> Any:
    """Await *probe*, failing it after *timeout_seconds* when a budget is set.

    ``None`` keeps the pre-budget behaviour (unbounded) for callers that pass
    nothing — the route always passes the configured budget.
    """
    if timeout_seconds is None:
        return await probe
    try:
        return await asyncio.wait_for(probe, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise TimeoutError(f"timed out after {timeout_seconds}s") from exc


async def check_ready(
    orchestrator: Orchestrator, *, timeout_seconds: float | None = None
) -> ReadinessReport:
    """Run all readiness checks and return an aggregated report.

    Checks performed:

    - **llm**: If the LLM client implements :class:`PingableLLMClient`, ``ping()``
      is called.  Otherwise the check result is ``"unknown"`` (degraded-but-not-failing).
    - **db**: If a :class:`~mangomas.adapters.storage.base.TurnRepository` is
      configured, ``list_turns(limit=1)`` is called as a liveness probe.
      When no repository is configured the result is ``"not_configured"``.

    *timeout_seconds* bounds **each** probe (``MANGOMAS_API__READY_TIMEOUT_SECONDS``,
    passed by the route from ``app.state`` — spec-0031 S4). A probe that hangs
    past it is reported as ``"error"`` with a ``timed out`` detail instead of
    holding an unauthenticated ``/readyz`` open for the dependency's own
    timeout (60 s for the LLM client by default).
    """
    report = ReadinessReport()
    ctx = orchestrator.context

    # ── LLM check ─────────────────────────────────────────────────────────────
    if isinstance(ctx.llm, PingableLLMClient):
        try:
            await _bounded(ctx.llm.ping(), timeout_seconds)
            report.checks.append(CheckResult(name="llm", status=_STATUS_OK))
            logger.debug("Readiness: LLM ping OK")
        except Exception as exc:  # broad: any ping failure degrades readiness
            # Truncated: /readyz is unauthenticated, so an upstream driver
            # message must not stream unbounded text into a public body.
            detail = str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE]
            report.checks.append(CheckResult(name="llm", status=_STATUS_ERROR, detail=detail))
            logger.warning("Readiness: LLM ping failed: %s", detail)
    else:
        report.checks.append(CheckResult(name="llm", status=_STATUS_UNKNOWN))
        logger.debug("Readiness: LLM client does not support ping")

    # ── DB check ──────────────────────────────────────────────────────────────
    if ctx.repo is None:
        report.checks.append(CheckResult(name="db", status=_STATUS_NOT_CONFIGURED))
        logger.debug("Readiness: no repository configured")
    else:
        try:
            await _bounded(ctx.repo.list_turns(limit=1), timeout_seconds)
            report.checks.append(CheckResult(name="db", status=_STATUS_OK))
            logger.debug("Readiness: DB probe OK")
        except Exception as exc:  # broad: any DB error degrades readiness
            # Truncated: /readyz is unauthenticated, so an upstream driver
            # message must not stream unbounded text into a public body.
            detail = str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE]
            report.checks.append(CheckResult(name="db", status=_STATUS_ERROR, detail=detail))
            logger.warning("Readiness: DB probe failed: %s", detail)

    return report
