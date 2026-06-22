"""Tests for telemetry bootstrap."""

from __future__ import annotations

import json
import logging

import pytest
from opentelemetry import trace

from mangomas import telemetry
from mangomas.config import TelemetrySettings
from mangomas.telemetry import JsonFormatter


def _reset() -> None:
    """Force the telemetry singleton back to unconfigured."""
    telemetry._state.configured = False


def test_configure_telemetry_idempotent() -> None:
    _reset()
    telemetry.configure_telemetry(service_name="test", log_level="DEBUG")
    first = telemetry._state.configured
    telemetry.configure_telemetry()
    assert first is True
    assert telemetry._state.configured is True


def test_configure_telemetry_none_preserves_console_default() -> None:
    """``telemetry=None`` keeps the historical console-only behaviour."""
    _reset()
    telemetry.configure_telemetry(service_name="test")
    assert telemetry._state.configured is True


def test_configure_telemetry_with_console_settings() -> None:
    """Passing a console ``TelemetrySettings`` configures without error."""
    _reset()
    telemetry.configure_telemetry(
        service_name="test", telemetry=TelemetrySettings(exporter="console")
    )
    tracer = telemetry.get_tracer("settings-console")
    with tracer.start_as_current_span("span") as span:
        span.set_attribute("k", "v")
    assert telemetry._state.configured is True


def test_configure_telemetry_json_format() -> None:
    """configure_telemetry with log_format='json' attaches a JsonFormatter."""
    _reset()
    telemetry.configure_telemetry(service_name="test", log_format="json")
    root_logger = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root_logger.handlers), (
        "Expected a JsonFormatter on the root handler"
    )


def test_get_tracer_returns_tracer() -> None:
    tracer = telemetry.get_tracer("unit")
    with tracer.start_as_current_span("span-x") as span:
        span.set_attribute("k", "v")
    assert tracer is not None


def test_get_tracer_does_not_auto_configure() -> None:
    """get_tracer() must NOT auto-configure (would lock in the console default).

    OTel's ProxyTracer delegates to the real provider once registered, so a
    module-level ``get_tracer(__name__)`` is safe before ``configure_telemetry``.
    """
    _reset()
    tracer = telemetry.get_tracer("cold-start-test")
    assert tracer is not None
    assert telemetry._state.configured is False


def test_flush_telemetry_calls_force_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    """flush_telemetry() force-flushes a provider that supports it."""
    calls: list[str] = []

    class _Provider:
        def force_flush(self) -> None:
            calls.append("flushed")

    monkeypatch.setattr(trace, "get_tracer_provider", _Provider)
    telemetry.flush_telemetry()
    assert calls == ["flushed"]


def test_flush_telemetry_noop_without_force_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider lacking force_flush (e.g. ProxyTracerProvider) is a safe no-op."""
    monkeypatch.setattr(trace, "get_tracer_provider", object)
    telemetry.flush_telemetry()  # must not raise


# ── JsonFormatter ─────────────────────────────────────────────────────────────


def test_json_formatter_produces_valid_json() -> None:
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    output = fmt.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "hello world"
    assert parsed["severity"] == "INFO"
    assert parsed["logger"] == "test"


def test_json_formatter_includes_exc_info() -> None:
    """Exception info is serialised into the JSON envelope."""
    fmt = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="an error",
            args=(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    output = fmt.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]


def test_json_formatter_forwards_extra_fields() -> None:
    """Extra fields passed via ``extra={}`` appear at the top level."""
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="with extras",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc123"
    output = fmt.format(record)
    parsed = json.loads(output)
    assert parsed.get("request_id") == "abc123"


@pytest.mark.parametrize(
    ("level", "expected_severity"),
    [("DEBUG", "DEBUG"), ("WARNING", "WARNING"), ("ERROR", "ERROR")],
)
def test_json_formatter_severity_levels(level: str, expected_severity: str) -> None:
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=getattr(logging, level),
        pathname=__file__,
        lineno=1,
        msg="msg",
        args=(),
        exc_info=None,
    )
    parsed = json.loads(fmt.format(record))
    assert parsed["severity"] == expected_severity
