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
from mangomas.api.correlation import (
    CorrelationFilter,
    correlation_id,
    generate_correlation_id,
    get_correlation_id,
    set_correlation_id,
)
from mangomas.api.middleware import _BAGGAGE_KEY
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Orchestrator
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
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post(
            "/agents/echo/invoke",
            json={"messages": []},
            headers={"X-Request-ID": "leak-check"},
        )
    # After the request returns, the contextvar in *this* task must not retain
    # the request-scoped value.
    assert get_correlation_id() is None
