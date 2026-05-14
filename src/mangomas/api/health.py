"""Readiness probe helpers: check LLM + DB connectivity and report status."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from mangomas.adapters.llm.base import PingableLLMClient
from mangomas.core.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

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


async def check_ready(orchestrator: Orchestrator) -> ReadinessReport:
    """Run all readiness checks and return an aggregated report.

    Checks performed:

    - **llm**: If the LLM client implements :class:`PingableLLMClient`, ``ping()``
      is called.  Otherwise the check result is ``"unknown"`` (degraded-but-not-failing).
    - **db**: If a :class:`~mangomas.adapters.storage.base.TurnRepository` is
      configured, ``list_turns(limit=1)`` is called as a liveness probe.
      When no repository is configured the result is ``"not_configured"``.
    """
    report = ReadinessReport()
    ctx = orchestrator.context

    # ── LLM check ─────────────────────────────────────────────────────────────
    if isinstance(ctx.llm, PingableLLMClient):
        try:
            await ctx.llm.ping()
            report.checks.append(CheckResult(name="llm", status=_STATUS_OK))
            logger.debug("Readiness: LLM ping OK")
        except Exception as exc:  # broad: any ping failure degrades readiness
            detail = str(exc)
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
            await ctx.repo.list_turns(limit=1)
            report.checks.append(CheckResult(name="db", status=_STATUS_OK))
            logger.debug("Readiness: DB probe OK")
        except Exception as exc:  # broad: any DB error degrades readiness
            detail = str(exc)
            report.checks.append(CheckResult(name="db", status=_STATUS_ERROR, detail=detail))
            logger.warning("Readiness: DB probe failed: %s", detail)

    return report
