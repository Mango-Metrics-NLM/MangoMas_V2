"""Tests for request backpressure middleware (spec 0011 / ADR-0015)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from mangomas.api.app import create_app
from mangomas.api.middleware import ConcurrencyLimitMiddleware, MaxBodySizeMiddleware
from mangomas.config import get_settings
from mangomas.core import Orchestrator
from tests.constants import BACKPRESSURE_MAX_BODY_BYTES, BACKPRESSURE_MAX_CONCURRENT

_MSG = {"messages": [{"role": "user", "content": "hi"}]}


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
    installed = {getattr(m.cls, "__name__", "") for m in app.user_middleware}
    assert ConcurrencyLimitMiddleware.__name__ in installed
    with TestClient(app) as client:
        # A single request is still served normally within the cap.
        r = client.post("/agents/chat/invoke", json=_MSG)
        assert r.status_code == 200
