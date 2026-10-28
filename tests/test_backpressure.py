"""Tests for request backpressure middleware (spec 0011 / ADR-0015)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from mangomas.api.app import create_app
from mangomas.api.health import PROBE_PATHS
from mangomas.api.middleware import ConcurrencyLimitMiddleware, MaxBodySizeMiddleware
from mangomas.api.routes.system import build_system_router
from mangomas.config import get_settings
from mangomas.core import Orchestrator
from tests.constants import BACKPRESSURE_MAX_BODY_BYTES, BACKPRESSURE_MAX_CONCURRENT

_MSG = {"messages": [{"role": "user", "content": "hi"}]}
# Test-scoped bound on the saturated-stream probe; no config counterpart.
_STREAM_TEST_TIMEOUT_S = 5.0


# ── MaxBodySizeMiddleware (isolation) ─────────────────────────────────────────


def test_max_body_size_rejects_oversized() -> None:
    app = FastAPI()
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=BACKPRESSURE_MAX_BODY_BYTES)

    @app.post("/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        big = client.post("/echo", content=b"x" * 100)
        assert big.status_code == 413
        assert big.json()["error"] == "request_too_large"
        small = client.post("/echo", content=b"xx")
        assert small.status_code == 200


def test_max_body_size_logs_warning_on_reject(caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=BACKPRESSURE_MAX_BODY_BYTES)

    @app.post("/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    with (
        caplog.at_level(logging.WARNING, logger="mangomas.api.middleware"),
        TestClient(app) as client,
    ):
        client.post("/echo", content=b"x" * 100)
    records = [r for r in caplog.records if r.name == "mangomas.api.middleware"]
    assert records
    rec = records[0]
    assert rec.levelno == logging.WARNING
    assert getattr(rec, "error", None) == "request_too_large"
    assert getattr(rec, "max_bytes", None) == BACKPRESSURE_MAX_BODY_BYTES
    assert getattr(rec, "path", None) == "/echo"
    assert getattr(rec, "status_code", None) == 413


async def test_max_body_size_ignores_non_numeric_content_length() -> None:
    # A malformed Content-Length passes through (no 500) rather than crashing.
    middleware = MaxBodySizeMiddleware(FastAPI(), max_bytes=BACKPRESSURE_MAX_BODY_BYTES)
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"content-length", b"not-a-number")],
    }
    called = {"passed": False}

    async def call_next(_request: Request) -> Response:
        called["passed"] = True
        return PlainTextResponse("ok")

    response = await middleware.dispatch(Request(scope), call_next)
    assert called["passed"] is True
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("raw", "expect_passthrough"),
    [
        # U+00B2 SUPERSCRIPT TWO: ``isdigit()`` is True but ``int()`` raises,
        # so the ``isdigit`` guard this replaced turned the documented
        # "unknown size" pass-through into an unhandled ValueError -> 500.
        (b"\xb2", True),
        # U+0663 ARABIC-INDIC DIGIT THREE: both ``isdecimal()`` and ``int()``
        # accept it, so it must still be parsed as a real length (3 is under
        # the limit, so it passes through for a different, correct reason).
        ("\u0663".encode(), True),
    ],
    ids=["superscript-two", "arabic-indic-three"],
)
async def test_max_body_size_digit_like_content_length_does_not_raise(
    raw: bytes, expect_passthrough: bool
) -> None:
    """Digit-like but non-decimal Content-Length must not escape as a 500.

    ASGI header values are raw bytes, so a client can put any byte here. The
    guard must accept exactly what ``int()`` accepts; anything else falls
    through to the documented "unknown size" path.
    """
    middleware = MaxBodySizeMiddleware(FastAPI(), max_bytes=BACKPRESSURE_MAX_BODY_BYTES)
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"content-length", raw)],
    }
    called = {"passed": False}

    async def call_next(_request: Request) -> Response:
        called["passed"] = True
        return PlainTextResponse("ok")

    response = await middleware.dispatch(Request(scope), call_next)
    assert called["passed"] is expect_passthrough
    assert response.status_code == 200


# ── ConcurrencyLimitMiddleware (isolation) ────────────────────────────────────


async def test_concurrency_limit_rejects_when_saturated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=1)

    @app.get("/slow")
    async def slow() -> dict[str, bool]:
        started.set()
        await release.wait()
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    with caplog.at_level(logging.WARNING, logger="mangomas.api.middleware"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.get("/slow"))
            await started.wait()  # first request is now in-flight (holds the only slot)
            second = await client.get("/slow")
            assert second.status_code == 503
            assert second.json()["error"] == "server_at_capacity"
            release.set()
            first_result = await first
            assert first_result.status_code == 200
    records = [r for r in caplog.records if r.name == "mangomas.api.middleware"]
    assert records
    rec = records[0]
    assert rec.levelno == logging.WARNING
    assert getattr(rec, "error", None) == "server_at_capacity"
    assert getattr(rec, "max_concurrent", None) == 1
    assert getattr(rec, "path", None) == "/slow"
    assert getattr(rec, "status_code", None) == 503


# ── Probe paths bypass the cap (spec-0038; Copilot review on PR #101) ─────────
# Cloud Run does not route its health probes through containerConcurrency, so
# with the app cap equal to it a saturated instance would 503 its own liveness
# probe and be restarted mid-request. Mutation (proved while writing): drop the
# ``exempt_paths`` branch in ``ConcurrencyLimitMiddleware.__call__`` and the
# probe request below is 503.


@pytest.mark.parametrize("probe", sorted(PROBE_PATHS))
async def test_saturated_limiter_still_serves_probe_paths(probe: str) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=1, exempt_paths=PROBE_PATHS)

    @app.get("/slow")
    async def slow() -> dict[str, bool]:
        started.set()
        await release.wait()
        return {"ok": True}

    @app.get(probe)
    async def probe_route() -> dict[str, str]:
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.get("/slow"))
        await started.wait()  # the only slot is held
        assert (await client.get("/slow")).status_code == 503  # the cap still bites
        assert (await client.get(probe)).status_code == 200  # ...but never a probe
        release.set()
        assert (await first).status_code == 200


async def test_exempt_paths_default_to_none() -> None:
    """Without the keyword the guard caps everything — the pre-existing contract."""
    started = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=1)

    @app.get("/slow")
    async def slow() -> dict[str, bool]:
        started.set()
        await release.wait()
        return {"ok": True}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.get("/slow"))
        await started.wait()
        assert (await client.get("/healthz")).status_code == 503
        release.set()
        await first


def test_probe_paths_are_exactly_the_system_router_probe_routes() -> None:
    """The exemption set and the mounted probe routes come from one constant.

    If a probe route were added under a path the set does not name, the
    saturation restart would come back for that one route only.
    """
    router_paths = {getattr(route, "path", None) for route in build_system_router().routes}
    assert router_paths >= PROBE_PATHS, PROBE_PATHS - router_paths
    # Every probe route is a GET with no body and no auth — the only shape
    # worth exempting; `/agents` is a data route and must stay capped.
    assert "/agents" in router_paths
    assert "/agents" not in PROBE_PATHS


# ── create_app wiring ─────────────────────────────────────────────────────────


def test_no_backpressure_by_default(orchestrator: Orchestrator) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        # A large body is accepted when no limit is configured (default 0 = off).
        r = client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "user", "content": "x" * 1000}]},
        )
        assert r.status_code == 200


def test_body_limit_wired_from_settings(
    orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANGOMAS_API__MAX_BODY_BYTES", "10")
    get_settings.cache_clear()
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            "/agents/chat/invoke",
            json={"messages": [{"role": "user", "content": "well over ten bytes"}]},
        )
        assert r.status_code == 413
        # A bodyless request (no Content-Length) passes the size guard untouched.
        assert client.get("/healthz").status_code == 200


def test_concurrency_guard_wired_from_settings(
    orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANGOMAS_API__MAX_CONCURRENT_REQUESTS", str(BACKPRESSURE_MAX_CONCURRENT))
    get_settings.cache_clear()
    app = create_app(orchestrator=orchestrator)
    # The guard is actually installed from settings (not a no-op wiring branch);
    # the 503 reject path itself is proven in the isolation test above.
    guards = [
        m
        for m in app.user_middleware
        if getattr(m.cls, "__name__", "") == ConcurrencyLimitMiddleware.__name__
    ]
    assert len(guards) == 1
    # create_app passes the probe exemption, so a saturated Cloud Run instance
    # still answers its liveness probe (spec-0038).
    assert guards[0].kwargs["exempt_paths"] == PROBE_PATHS
    with TestClient(app) as client:
        # A single request is still served normally within the cap.
        r = client.post("/agents/chat/invoke", json=_MSG)
        assert r.status_code == 200


# ── The slot covers the whole response body (branch audit, Tier A) ───────────
# ``BaseHTTPMiddleware.call_next`` resolves at ``http.response.start``, so a
# streaming body ran entirely after the slot was released and the limit never
# covered ``/agents/{name}/stream``. Mutation (proved while writing): the
# previous ``BaseHTTPMiddleware`` implementation makes the second request 200.


async def test_concurrency_limit_holds_the_slot_until_the_stream_body_finishes() -> None:
    first_chunk_sent = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=1)

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"first"
            first_chunk_sent.set()
            await release.wait()
            yield b"last"

        return StreamingResponse(body(), media_type="text/plain")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.get("/stream"))
        await first_chunk_sent.wait()  # headers + one chunk are out; body still open
        # Bounded: under the old BaseHTTPMiddleware the second request is
        # *admitted*, its body then blocks on ``release`` and the test would
        # hang instead of failing.
        second = await asyncio.wait_for(client.get("/stream"), timeout=_STREAM_TEST_TIMEOUT_S)
        assert second.status_code == 503
        assert second.json()["error"] == "server_at_capacity"
        release.set()
        first_result = await first
        assert first_result.status_code == 200
        assert first_result.content == b"firstlast"


async def test_concurrency_slot_is_released_after_the_body_drains() -> None:
    app = FastAPI()
    app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=1)

    @app.get("/one")
    async def one() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"x"

        return StreamingResponse(body(), media_type="text/plain")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            assert (await client.get("/one")).status_code == 200


async def test_concurrency_limit_passes_non_http_scopes_through() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.append(scope["type"])

    middleware = ConcurrencyLimitMiddleware(inner, max_concurrent=0)  # saturated for http

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(_: Message) -> None:
        return None

    await middleware({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"]
