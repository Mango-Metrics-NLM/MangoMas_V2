"""Tests for request backpressure middleware (spec 0011 / ADR-0015)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mangomas.api.app import create_app
from mangomas.api.middleware import ConcurrencyLimitMiddleware, MaxBodySizeMiddleware
from mangomas.config import get_settings
from mangomas.core import Orchestrator
from tests.constants import BACKPRESSURE_MAX_BODY_BYTES

_MSG = {"messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


# ── ConcurrencyLimitMiddleware (isolation) ────────────────────────────────────


async def test_concurrency_limit_rejects_when_saturated() -> None:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.get("/slow"))
        await started.wait()  # first request is now in-flight (holds the only slot)
        second = await client.get("/slow")
        assert second.status_code == 503
        assert second.json()["error"] == "too_many_requests"
        release.set()
        first_result = await first
        assert first_result.status_code == 200


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
    monkeypatch.setenv("MANGOMAS_API__MAX_CONCURRENT_REQUESTS", "2")
    get_settings.cache_clear()
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        # A single request is served normally within the cap (guard installed).
        r = client.post("/agents/chat/invoke", json=_MSG)
        assert r.status_code == 200
