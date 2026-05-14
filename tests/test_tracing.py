"""Tests for TraceMiddleware and TraceContextFilter."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from mangomas.api.tracing import TraceMiddleware
from mangomas.telemetry import TraceContextFilter

# ── Module-level span exporter ────────────────────────────────────────────────
# The OTel global provider can only be promoted once (ProxyTracerProvider →
# TracerProvider).  If another test module already promoted it we add our
# InMemorySpanExporter to that provider so spans still flow to _EXPORTER.

_EXPORTER: InMemorySpanExporter = InMemorySpanExporter()


def _init_exporter() -> None:
    p = trace.get_tracer_provider()
    if isinstance(p, TracerProvider):
        # Another module already set a real provider - piggyback on it.
        p.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    else:
        new_p = TracerProvider()
        new_p.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(new_p)


_init_exporter()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    """Minimal FastAPI app with TraceMiddleware and a /health stub."""
    app = FastAPI()
    app.add_middleware(TraceMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _make_client() -> tuple[TestClient, InMemorySpanExporter]:
    """Return a test client wired to the module-level span exporter."""
    return TestClient(_make_app(), raise_server_exceptions=True), _EXPORTER


# ── Middleware behaviour ──────────────────────────────────────────────────────


def test_trace_middleware_does_not_break_requests() -> None:
    client, _ = _make_client()
    r = client.get("/health")
    assert r.status_code == 200


def test_trace_middleware_creates_span() -> None:
    client, exporter = _make_client()
    exporter.clear()
    client.get("/health")
    spans = exporter.get_finished_spans()
    assert len(spans) >= 1
    span_names = [s.name for s in spans]
    assert any("health" in name.lower() or "GET" in name for name in span_names)


def test_trace_middleware_sets_http_attributes() -> None:
    client, exporter = _make_client()
    exporter.clear()
    client.get("/health")
    spans = exporter.get_finished_spans()
    attrs: dict[str, object] = {}
    for span in spans:
        if span.attributes is not None:
            attrs.update(span.attributes)
    assert attrs.get("http.method") == "GET"
    assert attrs.get("http.status_code") == 200


def test_trace_middleware_sets_error_on_5xx() -> None:
    """A route that raises an unhandled error → span status ERROR."""
    app = FastAPI()
    app.add_middleware(TraceMiddleware)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    _EXPORTER.clear()
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/boom")

    assert r.status_code == 500
    spans = _EXPORTER.get_finished_spans()
    error_spans = [s for s in spans if s.status.status_code == StatusCode.ERROR]
    assert len(error_spans) >= 1


# ── TraceContextFilter ────────────────────────────────────────────────────────


def test_trace_context_filter_sets_empty_strings_outside_span() -> None:
    """Outside a span the filter still returns True and sets empty fields."""
    filt = TraceContextFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    result = filt.filter(record)
    assert result is True
    assert hasattr(record, "trace_id")
    assert hasattr(record, "span_id")
    assert record.__dict__["trace_id"] == ""
    assert record.__dict__["span_id"] == ""


def test_trace_context_filter_injects_ids_within_span() -> None:
    """Inside an active span the filter injects non-empty trace/span IDs."""
    tracer = trace.get_tracer("test")
    filt = TraceContextFilter()

    with tracer.start_as_current_span("test-span"):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="inside",
            args=(),
            exc_info=None,
        )
        result = filt.filter(record)

    assert result is True
    trace_id = record.__dict__["trace_id"]
    span_id = record.__dict__["span_id"]
    assert trace_id != ""
    assert span_id != ""
    assert len(trace_id) == 32  # 128-bit hex
    assert len(span_id) == 16  # 64-bit hex
