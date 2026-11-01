"""Drive the Studio consumer contract end-to-end against the real app (spec-0041).

Studio lives in its own repository (ADR-0038). This probe is the in-tree
stand-in for its control-plane client: it calls the same HTTP surface a Studio
screen would and checks the contract each screen relies on::

    python scripts/studio_consumer_probe.py --mode mock
    python scripts/studio_consumer_probe.py --mode live --review-model <id>

``mock`` injects an in-process OpenAI-compatible stub through
``httpx.ASGITransport`` — no port, no LM Studio, and any real socket the app
opens fails the run. ``live`` points the same journeys at LM Studio; the
review model is required so the two profiles route to distinct models. A
check the mode cannot observe (which model served a live reply) is reported
as ``SKIP``, never as a pass.

The pieces live in sibling helpers: ``_studio_probe_model`` (types and
constants), ``_studio_probe_seams`` (process seams) and
``_studio_probe_journeys`` (one journey per Studio screen). This module
resolves the config, runs the journeys and reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import _stdio
import _studio_probe_journeys as journeys
import httpx
from _openai_compat_stub import FAIL_MODEL
from _studio_probe_journeys import JOURNEYS
from _studio_probe_model import (
    BROKEN_PROFILE,
    CHECK_FAILED,
    CHECK_OK,
    CHECK_SKIPPED,
    DRAFT_PROFILE,
    MODE_LIVE,
    MODE_MOCK,
    PROVIDER,
    REVIEW_PROFILE,
    STUDIO_ORIGIN,
    Check,
    Journey,
    JourneyContext,
    ProbeConfig,
    ProbeConfigError,
    Recorder,
)
from _studio_probe_seams import (
    BYPASS_ALL_PROXIES,
    CORS_ORIGINS_ENV,
    NO_PROXY_ENVS,
    cors_allow_origin,
    provider_seam,
    record_network_hosts,
)

from mangomas.api.app import create_app
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from mangomas.composition import build_orchestrator
from mangomas.config import DBSettings, LLMProfileSettings, LLMSettings, LoopSettings, Settings
from mangomas.config.llm import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
from mangomas.core import Orchestrator

__all__ = [
    "BYPASS_ALL_PROXIES",
    "CORS_ORIGINS_ENV",
    "JOURNEYS",
    "MODE_LIVE",
    "MODE_MOCK",
    "NO_PROXY_ENVS",
    "STUDIO_ORIGIN",
    "Check",
    "ProbeConfig",
    "ProbeConfigError",
    "build_orchestrator",
    "build_settings",
    "cors_allow_origin",
    "journeys",
    "main",
    "parse_args",
    "provider_seam",
    "record_network_hosts",
    "render",
    "resolve_config",
    "run_probe",
]

logger = logging.getLogger("studio_consumer_probe")

EXIT_OK: Final[int] = 0
APP_BASE_URL: Final[str] = "http://studio-probe"
STUB_BASE_URL: Final[str] = "http://openai-stub.invalid/v1"
STUB_DRAFT_MODEL: Final[str] = "stub-draft"
STUB_REVIEW_MODEL: Final[str] = "stub-review"
UNSERVED_API_VERSION: Final[str] = "/v999"
LIVE_BASE_URL_ENV: Final[str] = "LMSTUDIO_BASE_URL"
LIVE_MODEL_ENV: Final[str] = "LMSTUDIO_MODEL"
LIVE_REVIEW_MODEL_ENV: Final[str] = "LMSTUDIO_OVERRIDE_MODEL"
LIVE_TIMEOUT_ENV: Final[str] = "LMSTUDIO_E2E_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS: Final[float] = 240.0
CLIENT_TIMEOUT_HEADROOM_SECONDS: Final[float] = 30.0
IN_MEMORY_DB_URL: Final[str] = "sqlite:///:memory:"
LOG_LEVELS: Final[tuple[str, ...]] = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULT_LOG_LEVEL: Final[str] = "WARNING"
_LIVE_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_STATUS_LABELS: Final[dict[str, str]] = {
    CHECK_OK: "PASS",
    CHECK_FAILED: "FAIL",
    CHECK_SKIPPED: "SKIP",
}


# ── Configuration ────────────────────────────────────────────────────────────


def positive_float(raw: str) -> float:
    """argparse type: a finite number above zero (a zero budget never answers)."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from exc
    if not value > 0 or value == float("inf"):
        raise argparse.ArgumentTypeError(f"must be a finite number above zero: {raw!r}")
    return value


def _unserved_base_url(base_url: str) -> str:
    """Swap *base_url*'s path for an API version LM Studio does not serve."""
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, UNSERVED_API_VERSION, "", ""))


def _live_host(base_url: str) -> str:
    """The host a live run may contact; raise ProbeConfigError if there is none.

    ``urlsplit`` raises on a malformed authority (``http://[::1/v1``) and
    ``.port`` raises on a non-numeric or out-of-range port, so both are read
    here, before any journey runs, and reported as config errors (exit 2).
    """
    error = f"--base-url must be an http(s) URL with a host and a valid port: {base_url!r}"
    try:
        parts = urlsplit(base_url)
        _ = parts.port
    except ValueError as exc:
        raise ProbeConfigError(error) from exc
    if parts.scheme not in _LIVE_URL_SCHEMES or not parts.hostname:
        raise ProbeConfigError(error)
    return parts.hostname


def resolve_config(args: argparse.Namespace) -> ProbeConfig:
    """Build the run config for ``args.mode``; raise ProbeConfigError if incomplete."""
    if args.mode == MODE_MOCK:
        return ProbeConfig(
            mode=MODE_MOCK,
            base_url=STUB_BASE_URL,
            draft_model=STUB_DRAFT_MODEL,
            review_model=STUB_REVIEW_MODEL,
            broken_model=FAIL_MODEL,
            broken_base_url=None,
            timeout_seconds=args.timeout_seconds,
            allowed_hosts=frozenset(),
        )
    if not args.review_model:
        raise ProbeConfigError(f"live mode needs --review-model or {LIVE_REVIEW_MODEL_ENV}")
    host = _live_host(args.base_url)
    return ProbeConfig(
        mode=MODE_LIVE,
        base_url=args.base_url,
        draft_model=args.model,
        review_model=args.review_model,
        broken_model=args.model,
        broken_base_url=_unserved_base_url(args.base_url),
        timeout_seconds=args.timeout_seconds,
        allowed_hosts=frozenset({host}),
    )


def build_settings(cfg: ProbeConfig) -> Settings:
    """Three profiles over one provider: two that answer, one that cannot."""
    profiles = {
        DRAFT_PROFILE: LLMProfileSettings(provider=PROVIDER, model=cfg.draft_model),
        REVIEW_PROFILE: LLMProfileSettings(provider=PROVIDER, model=cfg.review_model),
        BROKEN_PROFILE: LLMProfileSettings(
            provider=PROVIDER, model=cfg.broken_model, base_url=cfg.broken_base_url
        ),
    }
    return Settings(
        llm=LLMSettings(
            provider=PROVIDER,
            base_url=cfg.base_url,
            model=cfg.draft_model,
            timeout_seconds=cfg.timeout_seconds,
        ),
        llm_profiles=profiles,
        db=DBSettings(provider="sqlite", url=IN_MEMORY_DB_URL),
        loop=LoopSettings(step_timeout_seconds=cfg.timeout_seconds),
    )


# ── Runner ───────────────────────────────────────────────────────────────────


async def _run_journey(journey: Journey, ctx: JourneyContext) -> None:
    ctx.rec.journey = journey.__name__.removeprefix("journey_")
    started = time.perf_counter()
    logger.debug("journey started", extra={"journey": ctx.rec.journey})
    try:
        await journey(ctx)
    except Exception as exc:  # a crashed journey is a failed check, not a crashed probe
        logger.warning("journey %s crashed: %s", ctx.rec.journey, exc, exc_info=True)
        ctx.rec.expect("journey completed", False, f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started
    logger.debug("journey %s finished in %.2fs", ctx.rec.journey, elapsed)


async def _drive(
    orch: Orchestrator, cfg: ProbeConfig, rec: Recorder, to_run: Sequence[Journey]
) -> None:
    timeout = cfg.timeout_seconds + CLIENT_TIMEOUT_HEADROOM_SECONDS
    transport = httpx.ASGITransport(app=create_app(orchestrator=orch))
    async with httpx.AsyncClient(
        transport=transport, base_url=APP_BASE_URL, timeout=timeout
    ) as client:
        ctx = JourneyContext(client=client, orch=orch, cfg=cfg, rec=rec)
        for journey in to_run:
            await _run_journey(journey, ctx)


async def run_probe(cfg: ProbeConfig, to_run: Sequence[Journey] = JOURNEYS) -> list[Check]:
    """Run *to_run* against a freshly built app and return every check."""
    rec = Recorder()
    logger.info("probe starting", extra={"mode": cfg.mode, "journeys": len(to_run)})
    with (
        cors_allow_origin(STUDIO_ORIGIN),
        record_network_hosts() as hosts,
        provider_seam(cfg) as stub_clients,
    ):
        try:
            orch = build_orchestrator(build_settings(cfg))
            try:
                await _drive(orch, cfg, rec, to_run)
            finally:
                await orch.aclose()
        finally:
            # Runs even when composition fails after a factory built a client.
            for http in stub_clients:
                await http.aclose()
    rec.journey = "network"
    rec.expect("only allowed hosts contacted", hosts <= cfg.allowed_hosts, str(sorted(hosts)))
    return rec.checks


# ── Reporting and CLI ────────────────────────────────────────────────────────


def render(checks: Sequence[Check]) -> str:
    """One line per check, then a tally of passes, failures and skips."""
    lines = [f"{_STATUS_LABELS[c.status]}  {c.journey}: {c.name}  {c.detail}" for c in checks]
    failed = sum(c.failed for c in checks)
    skipped = sum(c.status == CHECK_SKIPPED for c in checks)
    passed = len(checks) - failed - skipped
    lines.append(f"{passed}/{len(checks)} checks passed, {failed} failed, {skipped} skipped")
    return "\n".join(lines)


def write_report(path: Path, cfg: ProbeConfig, checks: Sequence[Check]) -> None:
    """Write the checks as JSON for CI artefacts; raises OSError on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mode": cfg.mode, "checks": [asdict(c) for c in checks]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the CLI; live-mode defaults come from the ``LMSTUDIO_*`` env vars.

    argparse applies ``type`` to a string default, so a malformed
    ``LMSTUDIO_E2E_TIMEOUT_SECONDS`` is a usage error (exit 2), not a traceback.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--mode", choices=(MODE_MOCK, MODE_LIVE), default=MODE_MOCK)
    parser.add_argument(
        "--base-url", default=os.environ.get(LIVE_BASE_URL_ENV, DEFAULT_LLM_BASE_URL)
    )
    parser.add_argument("--model", default=os.environ.get(LIVE_MODEL_ENV, DEFAULT_LLM_MODEL))
    parser.add_argument("--review-model", default=os.environ.get(LIVE_REVIEW_MODEL_ENV, ""))
    parser.add_argument(
        "--timeout-seconds",
        type=positive_float,
        default=os.environ.get(LIVE_TIMEOUT_ENV, str(DEFAULT_TIMEOUT_SECONDS)),
    )
    parser.add_argument("--report", type=Path, default=None, help="write the checks as JSON")
    parser.add_argument("--log-level", choices=LOG_LEVELS, default=DEFAULT_LOG_LEVEL)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 when no check fails, 1 when any fails, 2 on a config gap."""
    _stdio.configure_utf8_stdio()
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")
    try:
        cfg = resolve_config(args)
    except ProbeConfigError as exc:
        print(f"studio-probe: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    checks = asyncio.run(run_probe(cfg))
    print(render(checks))
    if args.report is not None:
        try:
            write_report(args.report, cfg, checks)
        except OSError as exc:
            print(f"studio-probe: cannot write report {args.report}: {exc}", file=sys.stderr)
            return EXIT_RUNTIME_ERROR
    return EXIT_RUNTIME_ERROR if any(c.failed for c in checks) else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
