"""Contract tests for spec-0049 (GCP demo): Cloud Trace export and JSON logging.

Validates:
1. Cloud Logging JSON schema structure (timestamp, severity, logger, message,
   trace_id, span_id, correlation_id).
2. TraceContextFilter trace/span ID injection during active spans
   (32-hex / 16-hex matching active span).
3. TraceContextFilter empty string fallback when no active span exists.
4. CorrelationFilter correlation_id injection and default '-' placeholder fallback.
5. Exception serialization into 'exception' field when exc_info=True.
6. Custom extra attributes forwarding to root JSON payload.
7. Severity level mapping across standard logging levels
   (DEBUG, INFO, WARNING, ERROR, CRITICAL).
8. Cloud Trace span exporter integration under MANGOMAS_TELEMETRY__EXPORTER=gcp
   (offline mock client).
9. W3C TraceContext propagation (inject/extract traceparent carrier headers).
10. CLI runtime configure_cli_logging bootstrap under GCP exporter and JSON format settings.
11. Missing gcp extra fail-closed guard (ImportError with install hint).
12. Gated live SDK verification under RUN_GCP_TRACE=1 (@pytest.mark.gcp_trace).
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import sys
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from mangomas import telemetry
from mangomas.cli._runtime import configure_cli_logging
from mangomas.config import LogSettings, Settings, TelemetrySettings
from mangomas.correlation import (
    CorrelationFilter,
    correlation_id,
    set_correlation_id,
)
from mangomas.telemetry.exporters import (
    _GCP_TRACE_INSTALL_HINT,
    _lazy_cloud_trace_exporter,
)
from mangomas.telemetry.logs import JsonFormatter, TraceContextFilter
from tests.constants import SETTINGS_ENV_PREFIX, TEST_VERTEX_PROJECT

# W3C Trace Context trace-flags bit 0: the span is sampled.
_W3C_SAMPLED_FLAG = 0x01


@contextlib.contextmanager
def _captured_json_logger(
    name: str,
    *,
    level: int = logging.INFO,
    with_filters: bool = True,
) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """Attach an isolated ``JsonFormatter`` stream handler and clean it up on exit."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    if with_filters:
        handler.addFilter(TraceContextFilter())
        handler.addFilter(CorrelationFilter())

    logger = logging.getLogger(name)
    old_level = logger.level
    old_propagate = logger.propagate
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        yield logger, buf
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


@pytest.fixture(autouse=True)
def reset_telemetry_and_logging() -> Iterator[None]:
    """Isolate telemetry singleton, scoped tracers, correlation ID, and root handlers."""
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    telemetry._state.configured = False
    telemetry._scoped_tracers.clear()
    token = correlation_id.set(None)
    try:
        yield
    finally:
        telemetry._state.configured = False
        telemetry._scoped_tracers.clear()
        root.handlers = old_handlers
        root.setLevel(old_level)
        correlation_id.reset(token)


def test_cloud_logging_json_schema_structure() -> None:
    """Cloud Logging JSON format emits all required schema fields."""
    test_message = "structured log test"
    with _captured_json_logger("test.contract.schema") as (logger, buf):
        logger.info(test_message)
        output = buf.getvalue().strip()

    data: dict[str, Any] = json.loads(output)

    # Core Cloud Logging fields
    assert "timestamp" in data
    assert isinstance(data["timestamp"], str)
    assert data["severity"] == "INFO"
    assert data["logger"] == "test.contract.schema"
    assert data["message"] == test_message

    # Distributed tracing / correlation fields
    assert "trace_id" in data
    assert isinstance(data["trace_id"], str)
    assert "span_id" in data
    assert isinstance(data["span_id"], str)
    assert "correlation_id" in data
    assert isinstance(data["correlation_id"], str)


def test_cloud_logging_trace_context_active_span() -> None:
    """During active span, trace_id is 32-hex and span_id is 16-hex matching active span."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    try:
        tracer = provider.get_tracer("test.tracer")
        with _captured_json_logger("test.contract.active_span") as (logger, buf):
            with tracer.start_as_current_span("test-operation") as span:
                ctx = span.get_span_context()
                expected_trace_id = format(ctx.trace_id, "032x")
                expected_span_id = format(ctx.span_id, "016x")
                logger.info("inside span")
            output = buf.getvalue().strip()
    finally:
        provider.shutdown()

    data: dict[str, Any] = json.loads(output)

    assert re.match(r"^[0-9a-f]{32}$", data["trace_id"])
    assert re.match(r"^[0-9a-f]{16}$", data["span_id"])
    assert data["trace_id"] == expected_trace_id
    assert data["span_id"] == expected_span_id


def test_cloud_logging_trace_context_inactive_span() -> None:
    """Outside any span, trace_id and span_id are empty strings."""
    with _captured_json_logger("test.contract.inactive_span") as (logger, buf):
        logger.info("outside span")
        output = buf.getvalue().strip()

    data: dict[str, Any] = json.loads(output)

    assert data["trace_id"] == ""
    assert data["span_id"] == ""


def test_cloud_logging_correlation_id_active_and_fallback() -> None:
    """correlation_id matches active ContextVar; defaults to '-' when unset."""
    with _captured_json_logger("test.contract.correlation") as (logger, buf):
        # 1. Unset fallback
        logger.info("no correlation")
        line1 = buf.getvalue().splitlines()[-1]
        data1: dict[str, Any] = json.loads(line1)
        assert data1["correlation_id"] == "-"

        # 2. Active correlation ID
        active_cid = "c0-corr-8f92"
        set_correlation_id(active_cid)
        logger.info("with correlation")
        line2 = buf.getvalue().splitlines()[-1]
        data2: dict[str, Any] = json.loads(line2)
        assert data2["correlation_id"] == active_cid


def test_cloud_logging_exception_serialization() -> None:
    """exc_info=True serializes traceback into the 'exception' JSON field."""
    err_msg = "simulated telemetry contract error"
    with _captured_json_logger("test.contract.exception", level=logging.ERROR) as (logger, buf):
        try:
            raise RuntimeError(err_msg)
        except RuntimeError:
            logger.exception("operation failed")
        output = buf.getvalue().strip()

    data: dict[str, Any] = json.loads(output)

    assert "exception" in data
    assert "RuntimeError" in data["exception"]
    assert err_msg in data["exception"]
    assert "Traceback (most recent call last):" in data["exception"]


def test_cloud_logging_extra_fields_forwarding() -> None:
    """Custom attributes in extra={...} are placed at the root level of JSON."""
    custom_extras: dict[str, Any] = {
        "user_id": "test-user-mc0",
        "turn_count": 3,
        "gcp_region": "us-central1",
        "dry_run": True,
    }
    with _captured_json_logger("test.contract.extra") as (logger, buf):
        logger.info("turn completed", extra=custom_extras)
        output = buf.getvalue().strip()

    data: dict[str, Any] = json.loads(output)

    for key, value in custom_extras.items():
        assert key in data
        assert data[key] == value


@pytest.mark.parametrize(
    ("level_name", "level_int"),
    [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ],
)
def test_cloud_logging_severity_levels(level_name: str, level_int: int) -> None:
    """Severity levels match standard logging levels."""
    with _captured_json_logger(
        f"test.contract.severity.{level_name}",
        level=logging.DEBUG,
        with_filters=False,
    ) as (logger, buf):
        logger.log(level_int, "message at level %s", level_name)
        output = buf.getvalue().strip()

    data: dict[str, Any] = json.loads(output)
    assert data["severity"] == level_name


@pytest.mark.gcp_trace
def test_cloud_trace_export_offline_mock_client() -> None:
    """CloudTraceSpanExporter translates spans and calls mock_client.batch_write_spans offline.

    Offline (mock client, no network) but it imports the real exporter class,
    which ships in the optional `gcp` extra — hence the `gcp_trace` gate.
    """
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter  # noqa: PLC0415

    mock_client = MagicMock()
    exporter_cls = cast(Any, CloudTraceSpanExporter)
    exporter = exporter_cls(project_id=TEST_VERTEX_PROJECT, client=mock_client)

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        tracer = provider.get_tracer("gcp.contract.test")

        span_name = "agent-reasoning-step"
        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("mangomas.model", "gemini-2.5-pro")
            span.set_attribute("mangomas.step", 1)
    finally:
        provider.shutdown()

    assert mock_client.batch_write_spans.called is True
    call_kwargs = mock_client.batch_write_spans.call_args.kwargs
    request = call_kwargs["request"]

    # Verify project name format
    assert request.name == f"projects/{TEST_VERTEX_PROJECT}"

    # Verify converted spans
    assert len(request.spans) == 1
    exported_span = request.spans[0]
    assert exported_span.display_name.value == span_name
    assert exported_span.name.startswith(f"projects/{TEST_VERTEX_PROJECT}/traces/")
    assert f"/spans/{format(span.get_span_context().span_id, '016x')}" in exported_span.name


def test_cloud_trace_w3c_propagation() -> None:
    """TraceContextTextMapPropagator injects and extracts W3C traceparent headers."""
    propagator = TraceContextTextMapPropagator()
    provider = TracerProvider()
    try:
        tracer = provider.get_tracer("test.w3c")

        # 1. Injection test
        carrier: dict[str, str] = {}
        with tracer.start_as_current_span("parent-span") as span:
            propagator.inject(carrier)
            ctx = span.get_span_context()
            expected_trace_id = format(ctx.trace_id, "032x")
            expected_span_id = format(ctx.span_id, "016x")
    finally:
        provider.shutdown()

    assert "traceparent" in carrier
    # W3C traceparent format: version-trace_id-parent_id-trace_flags
    parts = carrier["traceparent"].split("-")
    assert len(parts) == 4
    assert parts[0] == "00"
    assert parts[1] == expected_trace_id
    assert parts[2] == expected_span_id
    # W3C trace-flags is a bit field. Only bit 0 (sampled) is asserted: newer
    # OpenTelemetry SDKs also set bit 1 (W3C Level 2 "random trace-id"), so
    # the header reads "03", and an equality check on "01" breaks on upgrade.
    assert int(parts[3], 16) & _W3C_SAMPLED_FLAG == _W3C_SAMPLED_FLAG

    # 2. Extraction test
    incoming_carrier = {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    }
    extracted_context = propagator.extract(incoming_carrier)
    extracted_span_ctx = trace.get_current_span(extracted_context).get_span_context()
    assert extracted_span_ctx.is_valid is True
    assert format(extracted_span_ctx.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert format(extracted_span_ctx.span_id, "016x") == "00f067aa0ba902b7"


def test_configure_cli_logging_gcp_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """configure_cli_logging under GCP exporter and JSON format configures JsonFormatter."""
    for key in list(os.environ):
        if key.startswith(SETTINGS_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)

    # Mock settings with GCP exporter and JSON format
    test_settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        log=LogSettings(format="json"),
        telemetry=TelemetrySettings(exporter="gcp", metrics_enabled=False),
    )
    monkeypatch.setattr("mangomas.cli._runtime.get_settings", lambda: test_settings)

    # Use in-memory exporter so configure_telemetry does not instantiate live GCP SDK
    fake_exporter = InMemorySpanExporter()
    monkeypatch.setattr(telemetry.exporters, "_lazy_cloud_trace_exporter", lambda: fake_exporter)

    configure_cli_logging(verbose=False)

    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(json_handlers) >= 1, "Expected at least one handler with JsonFormatter"

    handler = json_handlers[0]
    filter_types = [type(f) for f in handler.filters]
    assert TraceContextFilter in filter_types
    assert CorrelationFilter in filter_types

    # Test formatting through the configured logger
    test_record = logging.LogRecord(
        name="cli.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="cli bootstrap test message",
        args=(),
        exc_info=None,
    )
    # Run handler filters and format record. Handler.filter returns a bool on
    # Python 3.11 but the (possibly replaced) record on 3.12+, so assert
    # truthiness, which is the contract both versions honour.
    assert handler.filter(test_record)
    assert handler.formatter is not None
    output = handler.formatter.format(test_record)
    parsed: dict[str, Any] = json.loads(output)
    assert parsed["message"] == "cli bootstrap test message"
    assert parsed["severity"] == "INFO"
    assert "trace_id" in parsed
    assert "span_id" in parsed
    assert "correlation_id" in parsed


def test_cloud_trace_missing_extra_raises_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulating missing opentelemetry.exporter.cloud_trace raises ImportError with hint."""
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.cloud_trace", None)

    with pytest.raises(ImportError, match=re.escape(_GCP_TRACE_INSTALL_HINT)):
        _lazy_cloud_trace_exporter()


@pytest.mark.gcp_trace
def test_cloud_trace_live_sdk_gated() -> None:
    """Live SDK test gated by RUN_GCP_TRACE=1. Validates real CloudTraceSpanExporter build."""
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter  # noqa: PLC0415

    exporter = _lazy_cloud_trace_exporter()
    assert isinstance(exporter, CloudTraceSpanExporter)
