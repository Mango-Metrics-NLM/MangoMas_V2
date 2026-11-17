"""Readiness probe helpers: check LLM + DB connectivity and report status."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

from mangomas.adapters.llm.base import PingableLLMClient
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import ConfigError
from mangomas.utils.json_path import finite_number

if TYPE_CHECKING:  # pragma: no cover
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


def _normalize_ready_timeout(timeout_seconds: float | None) -> float | None:
    """Validate and coerce *timeout_seconds* to a positive finite ``float``."""
    if timeout_seconds is None:
        return None
    normalized = finite_number(timeout_seconds)
    if normalized is None or normalized <= 0.0:
        raise ConfigError(
            f"ready_timeout_seconds must be a positive finite number; got {timeout_seconds!r}"
        )
    return normalized


async def _run_bounded_probe(
    probe_fn: Callable[[], Awaitable[Any]],
    *,
    name: str,
    failure_prefix: str,
    timeout_seconds: float | None,
    report: ReadinessReport,
) -> bool:
    """Invoke and await *probe_fn* within *timeout_seconds* and append its result."""
    try:
        if timeout_seconds is not None:
            await asyncio.wait_for(probe_fn(), timeout=timeout_seconds)
        else:
            await probe_fn()
        report.checks.append(CheckResult(name=name, status=_STATUS_OK))
        return True
    except Exception as exc:  # broad: any probe failure or timeout degrades readiness
        # Truncated: /readyz is unauthenticated, so an upstream driver message
        # must not stream unbounded text into a public body.
        if isinstance(exc, TimeoutError) and timeout_seconds is not None:
            timed_out = True
            detail = f"timed out after {timeout_seconds:g}s"
        else:
            timed_out = False
            detail = str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE]
        report.checks.append(CheckResult(name=name, status=_STATUS_ERROR, detail=detail))
        logger.warning(
            "Readiness: %s: %s",
            failure_prefix,
            detail,
            extra={
                "event": "readiness_probe_failed",
                "probe": name,
                "timed_out": timed_out,
                "detail": detail,
            },
        )
        return False


async def check_ready(
    orchestrator: Orchestrator,
    *,
    timeout_seconds: float | None = None,
) -> ReadinessReport:
    """Run all readiness checks and return an aggregated report.

    Checks performed:

    - **llm**: If the LLM client implements :class:`PingableLLMClient`, ``ping()``
      is called (bounded by *timeout_seconds* when given). Otherwise the check
      result is ``"unknown"`` (degraded-but-not-failing).
    - **db**: If a :class:`~mangomas.adapters.storage.base.TurnRepository` is
      configured, ``list_turns(limit=1)`` is called as a liveness probe
      (bounded by *timeout_seconds* when given). When no repository is
      configured the result is ``"not_configured"``.
    """
    budget = _normalize_ready_timeout(timeout_seconds)
    report = ReadinessReport()
    ctx = orchestrator.context

    # ── LLM check ─────────────────────────────────────────────────────────────
    if isinstance(ctx.llm, PingableLLMClient):
        llm = ctx.llm
        if await _run_bounded_probe(
            llm.ping,
            name="llm",
            failure_prefix="LLM ping failed",
            timeout_seconds=budget,
            report=report,
        ):
            logger.debug(
                "Readiness: LLM ping OK",
                extra={"event": "readiness_probe_ok", "probe": "llm"},
            )
    else:
        report.checks.append(CheckResult(name="llm", status=_STATUS_UNKNOWN))
        logger.debug(
            "Readiness: LLM client does not support ping",
            extra={
                "event": "readiness_probe_skipped",
                "probe": "llm",
                "status": _STATUS_UNKNOWN,
            },
        )

    # ── DB check ──────────────────────────────────────────────────────────────
    if ctx.repo is None:
        report.checks.append(CheckResult(name="db", status=_STATUS_NOT_CONFIGURED))
        logger.debug(
            "Readiness: no repository configured",
            extra={
                "event": "readiness_probe_skipped",
                "probe": "db",
                "status": _STATUS_NOT_CONFIGURED,
            },
        )
    else:
        repo = ctx.repo
        if await _run_bounded_probe(
            lambda: repo.list_turns(limit=1),
            name="db",
            failure_prefix="DB probe failed",
            timeout_seconds=budget,
            report=report,
        ):
            logger.debug(
                "Readiness: DB probe OK",
                extra={"event": "readiness_probe_ok", "probe": "db"},
            )

    logger.debug(
        "Readiness evaluation complete",
        extra={
            "event": "readiness_report",
            "ready": report.ready,
            "http_status": report.http_status,
        },
    )
    return report
