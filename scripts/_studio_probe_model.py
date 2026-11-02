"""Shared types and constants for the Studio consumer probe (spec-0041).

The leaf of the probe's helpers: the seams and journeys import from here and
never from each other, and nothing here imports a sibling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

import httpx

from mangomas.config.llm import LLM_PROFILE_METADATA_KEY
from mangomas.core import Orchestrator

MODE_MOCK: Final[str] = "mock"
MODE_LIVE: Final[str] = "live"
PROVIDER: Final[str] = "lmstudio"
DRAFT_PROFILE: Final[str] = "draft"
REVIEW_PROFILE: Final[str] = "review"
BROKEN_PROFILE: Final[str] = "broken"
UNKNOWN_PROFILE: Final[str] = "no-such-profile"
PROFILE_KEY: Final[str] = LLM_PROFILE_METADATA_KEY
STUDIO_ORIGIN: Final[str] = "http://localhost:5173"
UNLISTED_ORIGIN: Final[str] = "http://unlisted.example"
CHECK_OK: Final[str] = "pass"
CHECK_FAILED: Final[str] = "fail"
CHECK_SKIPPED: Final[str] = "skip"


class ProbeConfigError(ValueError):
    """The selected mode is missing a required input (exit 2)."""


@dataclass(frozen=True)
class ProbeConfig:
    """Everything one probe run needs; built by ``resolve_config``."""

    mode: str
    base_url: str
    draft_model: str
    review_model: str
    broken_model: str
    broken_base_url: str | None
    timeout_seconds: float
    allowed_hosts: frozenset[str]

    @property
    def echoes_model(self) -> bool:
        """Only the stub names the serving model in its reply."""
        return self.mode == MODE_MOCK


@dataclass(frozen=True)
class Check:
    """One contract assertion and its outcome.

    ``skip`` is for an assertion the current mode cannot observe. Reporting it
    as a pass would be a check that cannot fail.
    """

    journey: str
    name: str
    status: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        """Only a failure fails the run; a skip is reported, not counted."""
        return self.status == CHECK_FAILED


@dataclass
class Recorder:
    """Collects checks so every journey runs even after one fails."""

    checks: list[Check] = field(default_factory=list)
    journey: str = ""

    def expect(self, name: str, ok: bool, detail: str = "") -> None:
        """Record *name* as passed when *ok*, failed otherwise."""
        status = CHECK_OK if ok else CHECK_FAILED
        self.checks.append(Check(self.journey, name, status, detail))

    def skip(self, name: str, reason: str) -> None:
        """Record *name* as not observable in this mode."""
        self.checks.append(Check(self.journey, name, CHECK_SKIPPED, reason))


@dataclass(frozen=True)
class JourneyContext:
    """What a journey may touch: the HTTP client, the orchestrator, the config."""

    client: httpx.AsyncClient
    orch: Orchestrator
    cfg: ProbeConfig
    rec: Recorder


Journey = Callable[[JourneyContext], Awaitable[None]]
