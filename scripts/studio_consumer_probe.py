"""Drive the Studio consumer contract end-to-end against the real app (spec-0041).

Studio lives in its own repository (ADR-0038). This probe is the in-tree
stand-in for its control-plane client: it calls the same HTTP surface a Studio
screen would and checks the contract each screen relies on::

    python scripts/studio_consumer_probe.py --mode mock
    python scripts/studio_consumer_probe.py --mode live --review-model <id>

``mock`` injects an in-process OpenAI-compatible stub through
``httpx.ASGITransport`` — no port, no LM Studio, and any real socket the app
opens fails the run. ``live`` points the same journeys at LM Studio; the
review model is required so the two profiles route to distinct models.

Journeys: profile picker, invoke stamp, routing oracle, run timeline (stream
then ``GET /history``), unknown-profile 400 before any SSE header, workflow
validate/run through a ``concat`` fan_out, the eval report viewer (``echo``
target to ``json_file``), the CORS preflight, and the allowed-hosts guard.

The streamed turn's stored metadata carries no ``llm_profile`` (spec-0041
D-12). The probe pins that as a known limitation, so closing D-12 turns this
check red on purpose and the record gets updated with it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import _stdio
import httpx
from _openai_compat_stub import FAIL_MODEL, build_stub_app

import mangomas.eval.scorers
import mangomas.eval.sinks
import mangomas.eval.targets  # noqa: F401 — the three imports seed the eval registries
from mangomas.adapters.llm.lmstudio import LMStudioClient
from mangomas.api.app import create_app
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from mangomas.composition import build_orchestrator, llm_registry
from mangomas.config import (
    DBSettings,
    LLMProfileSettings,
    LLMSettings,
    LoopSettings,
    Settings,
    get_settings,
)
from mangomas.core import Orchestrator
from mangomas.core.agent import Message
from mangomas.eval import DatasetRow, EvalRunner, scorer_registry, sink_registry, target_registry

EXIT_OK = 0
MODE_MOCK = "mock"
MODE_LIVE = "live"
DRAFT_PROFILE = "draft"
REVIEW_PROFILE = "review"
BROKEN_PROFILE = "broken"
UNKNOWN_PROFILE = "no-such-profile"
PROFILE_KEY = "llm_profile"
STUDIO_ORIGIN = "http://localhost:5173"
UNLISTED_ORIGIN = "http://unlisted.example"
CORS_ORIGINS_ENV = "MANGOMAS_API__CORS_ALLOW_ORIGINS"
APP_BASE_URL = "http://studio-probe"
STUB_BASE_URL = "http://openai-stub.invalid/v1"
STUB_DRAFT_MODEL = "stub-draft"
STUB_REVIEW_MODEL = "stub-review"
UNSERVED_API_VERSION = "/v999"
LIVE_BASE_URL_ENV = "LMSTUDIO_BASE_URL"
LIVE_MODEL_ENV = "LMSTUDIO_MODEL"
LIVE_REVIEW_MODEL_ENV = "LMSTUDIO_OVERRIDE_MODEL"
LIVE_TIMEOUT_ENV = "LMSTUDIO_E2E_TIMEOUT_SECONDS"
DEFAULT_LIVE_BASE_URL = "http://localhost:1234/v1"
DEFAULT_LIVE_MODEL = "local-model"
DEFAULT_TIMEOUT_SECONDS = 240.0
CLIENT_TIMEOUT_HEADROOM_SECONDS = 30.0
IN_MEMORY_DB_URL = "sqlite:///:memory:"
PROMPT = "Reply with one short sentence about mangoes."
INVOKE_PROMPT = "Reply with one short sentence about mango orchards."
STREAM_PROMPT = "Reply with one short sentence about mango season."
HISTORY_SCAN_LIMIT = 20
SECRET_FIELDS = ("api_key", "secret_ref", "credentials_path")
_HTTP_BAD_REQUEST = 400
_HTTP_BAD_GATEWAY = 502
_SSE_DATA_PREFIX = "data: "


class ProbeConfigError(ValueError):
    """The selected mode is missing a required input (exit 2)."""


@dataclass(frozen=True)
class ProbeConfig:
    """Everything one probe run needs; built by :func:`resolve_config`."""

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
    """One contract assertion, reported whether it passed or not."""

    journey: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Recorder:
    """Collects checks so every journey runs even after one fails."""

    checks: list[Check] = field(default_factory=list)
    journey: str = ""

    def expect(self, name: str, ok: bool, detail: str = "") -> None:
        """Record *name* as passed when *ok*, with *detail* for the report."""
        self.checks.append(Check(self.journey, name, bool(ok), detail))


@dataclass(frozen=True)
class JourneyContext:
    """What a journey may touch: the HTTP client, the orchestrator, the config."""

    client: httpx.AsyncClient
    orch: Orchestrator
    cfg: ProbeConfig
    rec: Recorder


Journey = Callable[[JourneyContext], Awaitable[None]]


# ── Configuration ────────────────────────────────────────────────────────────


def _unserved_base_url(base_url: str) -> str:
    """Swap *base_url*'s path for an API version LM Studio does not serve."""
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, UNSERVED_API_VERSION, "", ""))


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
    return ProbeConfig(
        mode=MODE_LIVE,
        base_url=args.base_url,
        draft_model=args.model,
        review_model=args.review_model,
        broken_model=args.model,
        broken_base_url=_unserved_base_url(args.base_url),
        timeout_seconds=args.timeout_seconds,
        allowed_hosts=frozenset({urlsplit(args.base_url).hostname or ""}),
    )


def build_settings(cfg: ProbeConfig) -> Settings:
    """Three profiles over one provider: two that answer, one that cannot."""
    profiles = {
        DRAFT_PROFILE: LLMProfileSettings(provider="lmstudio", model=cfg.draft_model),
        REVIEW_PROFILE: LLMProfileSettings(provider="lmstudio", model=cfg.review_model),
        BROKEN_PROFILE: LLMProfileSettings(
            provider="lmstudio", model=cfg.broken_model, base_url=cfg.broken_base_url
        ),
    }
    return Settings(
        llm=LLMSettings(
            provider="lmstudio",
            base_url=cfg.base_url,
            model=cfg.draft_model,
            timeout_seconds=cfg.timeout_seconds,
        ),
        llm_profiles=profiles,
        db=DBSettings(provider="sqlite", url=IN_MEMORY_DB_URL),
        loop=LoopSettings(step_timeout_seconds=cfg.timeout_seconds),
    )


# ── Environment seams ────────────────────────────────────────────────────────


@contextmanager
def cors_allow_origin(origin: str) -> Iterator[None]:
    """Expose *origin* to ``create_app``, which reads CORS from ``get_settings``."""
    previous = os.environ.get(CORS_ORIGINS_ENV)
    os.environ[CORS_ORIGINS_ENV] = json.dumps([origin])
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(CORS_ORIGINS_ENV, None)
        else:
            os.environ[CORS_ORIGINS_ENV] = previous
        get_settings.cache_clear()


@contextmanager
def record_network_hosts() -> Iterator[set[str]]:
    """Record the host of every request that reaches a real httpx transport.

    ``ASGITransport`` traffic never reaches these classes, so in mock mode the
    set stays empty unless some component opened a socket on its own.
    """
    hosts: set[str] = set()
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_sync = httpx.HTTPTransport.handle_request

    async def _async(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        hosts.add(request.url.host)
        return await original_async(self, request)

    def _sync(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        hosts.add(request.url.host)
        return original_sync(self, request)

    httpx.AsyncHTTPTransport.handle_async_request = _async  # type: ignore[method-assign]
    httpx.HTTPTransport.handle_request = _sync  # type: ignore[method-assign]
    try:
        yield hosts
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = original_async  # type: ignore[method-assign]
        httpx.HTTPTransport.handle_request = original_sync  # type: ignore[method-assign]


@contextmanager
def stub_llm_provider(models: list[str]) -> Iterator[list[httpx.AsyncClient]]:
    """Route every ``lmstudio`` client the composition root builds to the stub.

    An injected httpx client is not owned by the adapter, so the yielded list
    lets the caller close each one after the orchestrator is closed.
    """
    stub = build_stub_app(models)
    clients: list[httpx.AsyncClient] = []

    def _factory(cfg: LLMSettings) -> LMStudioClient:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=stub), timeout=cfg.timeout_seconds
        )
        clients.append(http)
        return LMStudioClient(
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=cfg.api_key,
            timeout_seconds=cfg.timeout_seconds,
            default_temperature=cfg.temperature,
            client=http,
        )

    with llm_registry.scoped("lmstudio", _factory):
        yield clients


def _provider_seam(cfg: ProbeConfig) -> AbstractContextManager[list[httpx.AsyncClient]]:
    if cfg.mode == MODE_MOCK:
        return stub_llm_provider([cfg.draft_model, cfg.review_model])
    return nullcontext([])


# ── Journeys ─────────────────────────────────────────────────────────────────


def _request(content: str, profile: str | None = None) -> dict[str, Any]:
    metadata = {PROFILE_KEY: profile} if profile is not None else {}
    return {"messages": [{"role": "user", "content": content}], "metadata": metadata}


def _routed_to(cfg: ProbeConfig, content: str, model: str) -> tuple[bool, str]:
    """Whether *content* came from *model*; live replies cannot say, so pass."""
    if not cfg.echoes_model:
        return True, "live reply (model not observable)"
    return content.startswith(f"[{model}]"), content[:60]


async def _stored_turn(client: httpx.AsyncClient, prompt: str) -> dict[str, Any] | None:
    """The newest stored turn whose last message is *prompt*, if any.

    Binding to the prompt, not to "newest", keeps a check from passing on an
    older turn when the run under test persisted nothing.
    """
    resp = await client.get("/history", params={"limit": HISTORY_SCAN_LIMIT})
    for turn in resp.json().get("turns", []):
        messages = turn.get("request", {}).get("messages", [])
        if messages and messages[-1].get("content") == prompt:
            return dict(turn)
    return None


def _turn_metadata(turn: dict[str, Any] | None) -> dict[str, Any]:
    return dict(turn["response"].get("metadata", {})) if turn else {}


async def journey_profile_picker(ctx: JourneyContext) -> None:
    """The model picker lists the roster and never a credential."""
    resp = await ctx.client.get("/llm/profiles")
    body = resp.json()
    names = sorted(p["name"] for p in body.get("profiles", []))
    ctx.rec.expect("status 200", resp.status_code == httpx.codes.OK, str(resp.status_code))
    ctx.rec.expect(
        "roster", names == sorted([BROKEN_PROFILE, DRAFT_PROFILE, REVIEW_PROFILE]), str(names)
    )
    leaked = [f for f in SECRET_FIELDS if f in resp.text]
    ctx.rec.expect("no secret fields", not leaked, str(leaked))


async def journey_invoke_stamp(ctx: JourneyContext) -> None:
    """An invoke answers from the chosen profile and the stored turn says so."""
    payload = _request(INVOKE_PROMPT, REVIEW_PROFILE)
    resp = await ctx.client.post("/agents/chat/invoke", json=payload)
    body = resp.json()
    ctx.rec.expect("status 200", resp.status_code == httpx.codes.OK, str(resp.status_code))
    stamp = body.get("metadata", {}).get(PROFILE_KEY)
    ctx.rec.expect("response stamp", stamp == REVIEW_PROFILE, str(stamp))
    routed, detail = _routed_to(ctx.cfg, str(body.get("content", "")), ctx.cfg.review_model)
    ctx.rec.expect("served by the review model", routed, detail)
    stored = _turn_metadata(await _stored_turn(ctx.client, INVOKE_PROMPT)).get(PROFILE_KEY)
    ctx.rec.expect("history stamp", stored == REVIEW_PROFILE, str(stored))


async def journey_routing_oracle(ctx: JourneyContext) -> None:
    """The broken profile fails while the default client still answers."""
    broken = await ctx.client.post("/agents/chat/invoke", json=_request(PROMPT, BROKEN_PROFILE))
    code = broken.json().get("error")
    ctx.rec.expect(
        "broken profile is 502 llm_bad_response",
        broken.status_code == _HTTP_BAD_GATEWAY and code == "llm_bad_response",
        f"{broken.status_code} {code}",
    )
    default = await ctx.client.post("/agents/chat/invoke", json=_request(PROMPT))
    body = default.json()
    ctx.rec.expect(
        "default answers", default.status_code == httpx.codes.OK, str(default.status_code)
    )
    ctx.rec.expect("default is unstamped", PROFILE_KEY not in body.get("metadata", {}))
    routed, detail = _routed_to(ctx.cfg, str(body.get("content", "")), ctx.cfg.draft_model)
    ctx.rec.expect("served by the base model", routed, detail)


def _sse_events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len(_SSE_DATA_PREFIX) :])
        for line in text.splitlines()
        if line.startswith(_SSE_DATA_PREFIX)
    ]


async def journey_run_timeline(ctx: JourneyContext) -> None:
    """The stream names its profile up front; the stored turn does not (D-12)."""
    payload = _request(STREAM_PROMPT, REVIEW_PROFILE)
    resp = await ctx.client.post("/agents/chat/stream", json=payload)
    events = _sse_events(resp.text)
    kinds = [e.get("event") for e in events]
    ctx.rec.expect("status 200", resp.status_code == httpx.codes.OK, str(resp.status_code))
    frames = [e.get("data", {}) for e in events if e.get("event") == "metadata"]
    ctx.rec.expect(
        "one metadata frame names the profile",
        len(frames) == 1 and frames[0].get(PROFILE_KEY) == REVIEW_PROFILE,
        json.dumps(frames)[:120],
    )
    ctx.rec.expect("tokens then done", "token" in kinds and kinds[-1] == "done", str(kinds[-3:]))
    streamed = "".join(
        str(e.get("data", {}).get("content", "")) for e in events if e.get("event") == "token"
    )
    routed, detail = _routed_to(ctx.cfg, streamed, ctx.cfg.review_model)
    ctx.rec.expect("tokens served by the review model", routed, detail)
    turn = await _stored_turn(ctx.client, STREAM_PROMPT)
    ctx.rec.expect("streamed turn persisted", turn is not None)
    stored = _turn_metadata(turn)
    ctx.rec.expect(
        "D-12 limitation: streamed turn is unstamped (update spec-0041 if this fails)",
        turn is not None and PROFILE_KEY not in stored,
        json.dumps(stored)[:120],
    )


async def journey_unknown_profile(ctx: JourneyContext) -> None:
    """An unknown profile is a JSON 400 before any SSE header is sent."""
    resp = await ctx.client.post("/agents/chat/stream", json=_request(PROMPT, UNKNOWN_PROFILE))
    content_type = resp.headers.get("content-type", "")
    ctx.rec.expect("status 400", resp.status_code == _HTTP_BAD_REQUEST, str(resp.status_code))
    ctx.rec.expect("JSON, not SSE", content_type.startswith("application/json"), content_type)
    code = resp.json().get("error") if "json" in content_type else None
    ctx.rec.expect("config_error envelope", code == "config_error", str(code))


WORKFLOW_GRAPH: dict[str, Any] = {
    "schema_version": 1,
    "name": "studio-draft-then-review",
    "root": {
        "kind": "sequence",
        "steps": [
            {
                "kind": "fan_out",
                "join": "concat",
                "branches": [
                    {"kind": "agent", "agent": "chat"},
                    {"kind": "agent", "agent": "summarize"},
                ],
            },
            {"kind": "agent", "agent": "chat"},
        ],
    },
}


async def journey_workflow(ctx: JourneyContext) -> None:
    """The template validates, then runs with the profile carried to the end."""
    definition = json.dumps(WORKFLOW_GRAPH)
    check = await ctx.client.post("/workflows/validate", json={"definition": definition})
    root = check.json().get("root_kind")
    ctx.rec.expect(
        "validate ok", check.status_code == httpx.codes.OK and root == "sequence", str(root)
    )
    run = await ctx.client.post(
        "/workflows/run",
        json={"definition": definition, "request": _request(PROMPT, REVIEW_PROFILE)},
    )
    body = run.json()
    ctx.rec.expect("run 200", run.status_code == httpx.codes.OK, str(run.status_code))
    stamp = body.get("metadata", {}).get(PROFILE_KEY)
    ctx.rec.expect("profile survives concat fan_out", stamp == REVIEW_PROFILE, str(stamp))
    routed, detail = _routed_to(ctx.cfg, str(body.get("content", "")), ctx.cfg.review_model)
    ctx.rec.expect("final stage served by the review model", routed, detail)


EVAL_ROWS = (("r1", "mango", "mango"), ("r2", "papaya", "guava"))


async def journey_eval_report(ctx: JourneyContext) -> None:
    """The report viewer reads a ``json_file`` report with per-row verdicts."""
    rows = [
        DatasetRow(id=row_id, messages=[Message(role="user", content=prompt)], expected=expected)
        for row_id, prompt, expected in EVAL_ROWS
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.json"
        report = await EvalRunner(ctx.orch, scorer_registry.get("exact_match")({})).run(
            rows, target=target_registry.get("echo")({})
        )
        await sink_registry.get("json_file")({"path": str(path)}).emit(report)
        payload = json.loads(path.read_text(encoding="utf-8"))
    ctx.rec.expect("report written", isinstance(payload, dict), type(payload).__name__)
    ctx.rec.expect("row count", len(payload.get("rows", [])) == len(EVAL_ROWS))
    ctx.rec.expect("one pass, one fail", payload.get("passed") == 1, str(payload.get("passed")))


async def _preflight(client: httpx.AsyncClient, origin: str) -> httpx.Response:
    return await client.options(
        "/agents/chat/invoke",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


async def journey_cors(ctx: JourneyContext) -> None:
    """The browser plane: the Studio origin is allowed, any other is refused."""
    listed = await _preflight(ctx.client, STUDIO_ORIGIN)
    allowed = listed.headers.get("access-control-allow-origin")
    ctx.rec.expect("listed origin allowed", allowed == STUDIO_ORIGIN, str(allowed))
    unlisted = await _preflight(ctx.client, UNLISTED_ORIGIN)
    echoed = unlisted.headers.get("access-control-allow-origin")
    ctx.rec.expect(
        "unlisted origin refused",
        unlisted.status_code == _HTTP_BAD_REQUEST and echoed is None,
        f"{unlisted.status_code} {echoed}",
    )


JOURNEYS: tuple[Journey, ...] = (
    journey_profile_picker,
    journey_invoke_stamp,
    journey_routing_oracle,
    journey_run_timeline,
    journey_unknown_profile,
    journey_workflow,
    journey_eval_report,
    journey_cors,
)


# ── Runner ───────────────────────────────────────────────────────────────────


async def _run_journey(journey: Journey, ctx: JourneyContext) -> None:
    ctx.rec.journey = journey.__name__.removeprefix("journey_")
    try:
        await journey(ctx)
    except Exception as exc:  # a crashed journey is a failed check, not a crashed probe
        ctx.rec.expect("journey completed", False, f"{type(exc).__name__}: {exc}")


async def run_probe(cfg: ProbeConfig, journeys: Sequence[Journey] = JOURNEYS) -> list[Check]:
    """Run *journeys* against a freshly built app and return every check."""
    rec = Recorder()
    with (
        cors_allow_origin(STUDIO_ORIGIN),
        record_network_hosts() as hosts,
        _provider_seam(cfg) as stub_clients,
    ):
        try:
            orch = build_orchestrator(build_settings(cfg))
            try:
                await _drive(orch, cfg, rec, journeys)
            finally:
                await orch.aclose()
        finally:
            # Runs even when composition fails after a factory built a client.
            for http in stub_clients:
                await http.aclose()
    rec.journey = "network"
    rec.expect("only allowed hosts contacted", hosts <= cfg.allowed_hosts, str(sorted(hosts)))
    return rec.checks


async def _drive(
    orch: Orchestrator, cfg: ProbeConfig, rec: Recorder, journeys: Sequence[Journey]
) -> None:
    timeout = cfg.timeout_seconds + CLIENT_TIMEOUT_HEADROOM_SECONDS
    app = create_app(orchestrator=orch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=APP_BASE_URL, timeout=timeout
    ) as client:
        ctx = JourneyContext(client=client, orch=orch, cfg=cfg, rec=rec)
        for journey in journeys:
            await _run_journey(journey, ctx)


def render(checks: Sequence[Check]) -> str:
    """One line per check, then a pass/fail tally."""
    lines = [f"{'PASS' if c.ok else 'FAIL'}  {c.journey}: {c.name}  {c.detail}" for c in checks]
    failed = sum(not c.ok for c in checks)
    lines.append(f"{len(checks) - failed}/{len(checks)} checks passed")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the CLI; live-mode defaults come from the ``LMSTUDIO_*`` env vars."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--mode", choices=(MODE_MOCK, MODE_LIVE), default=MODE_MOCK)
    parser.add_argument(
        "--base-url", default=os.environ.get(LIVE_BASE_URL_ENV, DEFAULT_LIVE_BASE_URL)
    )
    parser.add_argument("--model", default=os.environ.get(LIVE_MODEL_ENV, DEFAULT_LIVE_MODEL))
    parser.add_argument("--review-model", default=os.environ.get(LIVE_REVIEW_MODEL_ENV, ""))
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get(LIVE_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)),
    )
    parser.add_argument("--report", type=Path, default=None, help="write the checks as JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 when every check passes, 1 when any fails, 2 on a config gap."""
    _stdio.configure_utf8_stdio()
    args = parse_args(argv)
    try:
        cfg = resolve_config(args)
    except ProbeConfigError as exc:
        print(f"studio-probe: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    checks = asyncio.run(run_probe(cfg))
    print(render(checks))
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        payload = {"mode": cfg.mode, "checks": [asdict(c) for c in checks]}
        args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return EXIT_OK if all(c.ok for c in checks) else EXIT_RUNTIME_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
