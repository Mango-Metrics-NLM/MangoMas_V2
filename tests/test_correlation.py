"""Tests for per-request correlation IDs.

Covers the ContextVar plumbing, the logging filter, the middleware's
inbound header consumption + outbound echo, and OTel baggage propagation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import baggage

from mangomas.api.app import create_app
from mangomas.api.middleware import _BAGGAGE_KEY
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Orchestrator
from mangomas.correlation import (
    MAX_CORRELATION_ID_LENGTH,
    CorrelationFilter,
    correlation_id,
    generate_correlation_id,
    get_correlation_id,
    set_correlation_id,
)
from mangomas.errors import LLMBadResponse
from tests.constants import ASGI_TEST_BASE_URL
from tests.fakes import FakeLLM

# ── ContextVar plumbing ───────────────────────────────────────────────────────


def test_get_returns_none_by_default() -> None:
    # Use a fresh token to isolate this test from anything pytest-async leaks.
    token = correlation_id.set(None)
    try:
        assert get_correlation_id() is None
    finally:
        correlation_id.reset(token)


def test_set_and_get_roundtrip() -> None:
    token = correlation_id.set(None)
    try:
        set_correlation_id("abc12345")
        assert get_correlation_id() == "abc12345"
    finally:
        correlation_id.reset(token)


def test_generate_returns_eight_hex_chars() -> None:
    value = generate_correlation_id()
    assert len(value) == 8
    int(value, 16)  # must parse as hex


# ── Logging filter ────────────────────────────────────────────────────────────


def _make_record(name: str = "test") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


def test_filter_injects_dash_when_no_correlation_active() -> None:
    token = correlation_id.set(None)
    try:
        record = _make_record()
        CorrelationFilter().filter(record)
        assert record.correlation_id == "-"  # type: ignore[attr-defined]
    finally:
        correlation_id.reset(token)


def test_filter_injects_active_correlation() -> None:
    token = correlation_id.set("xyz98765")
    try:
        record = _make_record()
        CorrelationFilter().filter(record)
        assert record.correlation_id == "xyz98765"  # type: ignore[attr-defined]
    finally:
        correlation_id.reset(token)


# ── Middleware integration ────────────────────────────────────────────────────


class _EchoAgent:
    """Minimal agent that captures the correlation id at handle() time."""

    name = "echo"
    captured_correlation: str | None = None
    captured_baggage: str | None = None

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:  # noqa: ARG002
        type(self).captured_correlation = get_correlation_id()
        type(self).captured_baggage = baggage.get_baggage(_BAGGAGE_KEY)  # type: ignore[assignment]
        return AgentResponse(content="ok", agent=self.name)


@pytest.fixture
def _correlation_app() -> Iterator[FastAPI]:
    llm = FakeLLM()
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(_EchoAgent())
    app = create_app(orchestrator=orch)
    _EchoAgent.captured_correlation = None
    _EchoAgent.captured_baggage = None
    yield app


async def test_middleware_generates_correlation_when_header_absent(
    _correlation_app: FastAPI,
) -> None:
    transport = httpx.ASGITransport(app=_correlation_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post("/agents/echo/invoke", json={"messages": []})

    assert response.status_code == 200, response.text
    echoed = response.headers.get("x-request-id")
    assert echoed and len(echoed) == 8
    assert _EchoAgent.captured_correlation == echoed
    assert _EchoAgent.captured_baggage == echoed


async def test_middleware_echoes_inbound_correlation_header(
    _correlation_app: FastAPI,
) -> None:
    transport = httpx.ASGITransport(app=_correlation_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/echo/invoke",
            json={"messages": []},
            headers={"X-Request-ID": "external-id-123"},
        )

    assert response.status_code == 200, response.text
    assert response.headers.get("x-request-id") == "external-id-123"
    assert _EchoAgent.captured_correlation == "external-id-123"
    assert _EchoAgent.captured_baggage == "external-id-123"


async def test_middleware_ignores_blank_inbound_header(_correlation_app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=_correlation_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/echo/invoke",
            json={"messages": []},
            headers={"X-Request-ID": "   "},
        )

    echoed = response.headers.get("x-request-id")
    assert echoed and echoed.strip() and echoed != "   "
    assert len(echoed) == 8


async def test_middleware_resets_correlation_after_request(
    _correlation_app: FastAPI,
) -> None:
    transport = httpx.ASGITransport(app=_correlation_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        await client.post(
            "/agents/echo/invoke",
            json={"messages": []},
            headers={"X-Request-ID": "leak-check"},
        )
    # After the request returns, the contextvar in *this* task must not retain
    # the request-scoped value.
    assert get_correlation_id() is None


# ── Middleware exception path ─────────────────────────────────────────────────


class _BoomAgent:
    """Agent whose ``handle()`` raises a non-MangomasError unhandled exception.

    Used to drive ``AccessLogMiddleware``'s ``except`` branch — the code
    path that logs ``unhandled exception`` and re-raises with the
    correlation id attached.
    """

    name = "boom"

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:  # noqa: ARG002
        raise RuntimeError("intentional test failure inside handler")


@pytest.fixture
def _boom_app() -> Iterator[FastAPI]:
    llm = FakeLLM()
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(_BoomAgent())
    yield create_app(orchestrator=orch)


async def test_middleware_logs_unhandled_exception_with_correlation(
    _boom_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ``except Exception`` branch must log + return a 500 with X-Request-ID."""
    transport = httpx.ASGITransport(app=_boom_app, raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="mangomas.api.middleware"):
        async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
            response = await client.post(
                "/agents/boom/invoke",
                json={"messages": []},
                headers={"X-Request-ID": "boom-trace-id"},
            )

    # Middleware synthesises its own 500 (so it can attach the header).
    assert response.status_code == 500, response.text
    # X-Request-ID must be echoed even on the unhandled-error response.
    assert response.headers.get("x-request-id") == "boom-trace-id"
    # The middleware's structured exception log must have fired.
    unhandled = [rec for rec in caplog.records if "unhandled exception" in rec.message]
    assert unhandled, "expected an 'unhandled exception' log line from the middleware"
    # Correlation id must be on the record (set via ``extra=...``).
    assert any(getattr(rec, "correlation_id", None) == "boom-trace-id" for rec in unhandled)


# ── X-Request-ID echo on FastAPI handled errors ──────────────────────────────


class _MangomasErrorAgent:
    """Agent that raises a ``MangomasError`` (handled by FastAPI exception handler)."""

    name = "explode"

    async def handle(self, request: AgentRequest, ctx: AgentContext) -> AgentResponse:  # noqa: ARG002
        raise LLMBadResponse("synthetic bad response for header-echo test")


@pytest.fixture
def _mangomas_error_app() -> Iterator[FastAPI]:
    llm = FakeLLM()
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(_MangomasErrorAgent())
    yield create_app(orchestrator=orch)


async def test_x_request_id_is_echoed_on_handled_error_response(
    _mangomas_error_app: FastAPI,
) -> None:
    """``MangomasError`` -> JSONResponse via FastAPI handler must echo X-Request-ID."""
    transport = httpx.ASGITransport(app=_mangomas_error_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/explode/invoke",
            json={"messages": []},
            headers={"X-Request-ID": "handled-error-id"},
        )

    assert response.status_code == 502, response.text
    # The handler returned a JSONResponse; middleware's finally must have
    # attached X-Request-ID to it before the response went out.
    assert response.headers.get("x-request-id") == "handled-error-id"


async def test_x_request_id_is_echoed_when_inbound_header_is_sanitised(
    _correlation_app: FastAPI,
) -> None:
    """An inbound value with CR/LF/control chars is sanitised before being echoed."""
    transport = httpx.ASGITransport(app=_correlation_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/echo/invoke",
            json={"messages": []},
            headers={"X-Request-ID": "good\r\nINJECT FAKE-LOG"},
        )

    echoed = response.headers.get("x-request-id")
    # CR/LF gone, but the legitimate prefix survives (spaces also stripped).
    assert echoed == "goodINJECTFAKE-LOG"
    assert "\r" not in (echoed or "")
    assert "\n" not in (echoed or "")


async def test_x_request_id_is_truncated_when_inbound_is_oversize(
    _correlation_app: FastAPI,
) -> None:
    """An oversize inbound id is clamped to MAX_CORRELATION_ID_LENGTH."""
    oversize = "x" * (MAX_CORRELATION_ID_LENGTH + 100)
    transport = httpx.ASGITransport(app=_correlation_app)
    async with httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client:
        response = await client.post(
            "/agents/echo/invoke",
            json={"messages": []},
            headers={"X-Request-ID": oversize},
        )

    echoed = response.headers.get("x-request-id")
    assert echoed is not None
    assert len(echoed) == MAX_CORRELATION_ID_LENGTH
