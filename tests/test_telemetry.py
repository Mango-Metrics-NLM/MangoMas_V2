"""Tests for telemetry bootstrap."""

from __future__ import annotations

import json
import logging
import subprocess
import sys

import pytest
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas import telemetry
from mangomas.errors import ConfigError
from mangomas.telemetry import JsonFormatter


def _reset() -> None:
    """Force the telemetry singleton back to unconfigured and clear scoped caches."""
    telemetry._state.configured = False
    telemetry._scoped_tracers.clear()


def test_configure_telemetry_idempotent() -> None:
    _reset()
    telemetry.configure_telemetry(service_name="test", log_level="DEBUG")
    first = telemetry._state.configured
    telemetry.configure_telemetry()
    assert first is True
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


def test_get_tracer_cold_start() -> None:
    """get_tracer() auto-configures telemetry when not yet configured."""
    _reset()
    tracer = telemetry.get_tracer("cold-start-test")
    assert tracer is not None
    assert telemetry._state.configured is True


# ── Exporter selection ────────────────────────────────────────────────────────


def test_build_span_exporter_console_default() -> None:
    """The default token yields the built-in console exporter."""
    exporter = telemetry._build_span_exporter(telemetry.EXPORTER_CONSOLE)
    assert isinstance(exporter, ConsoleSpanExporter)


def test_build_span_exporter_gcp_uses_lazy_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gcp token delegates to the lazy Cloud Trace helper (no SDK needed)."""
    sentinel = object()
    monkeypatch.setattr(telemetry.exporters, "_lazy_cloud_trace_exporter", lambda: sentinel)
    assert telemetry._build_span_exporter(telemetry.EXPORTER_GCP) is sentinel


def test_build_span_exporter_unknown_token_raises() -> None:
    """An unknown exporter token fails loud rather than silently using console."""
    with pytest.raises(ConfigError):
        telemetry._build_span_exporter("bogus")


def test_configure_telemetry_gcp_exporter_builds_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_telemetry(exporter='gcp') constructs the Cloud Trace exporter."""
    called: list[bool] = []

    def _fake_lazy() -> InMemorySpanExporter:
        called.append(True)
        return InMemorySpanExporter()

    monkeypatch.setattr(telemetry.exporters, "_lazy_cloud_trace_exporter", _fake_lazy)
    _reset()
    telemetry.configure_telemetry(service_name="t", exporter=telemetry.EXPORTER_GCP)
    assert called == [True]
    assert telemetry._state.configured is True


def test_build_scoped_tracer_inherit_returns_global() -> None:
    """The default 'inherit' path reuses the global provider (no behaviour change)."""
    _reset()
    tracer = telemetry.build_scoped_tracer("harness.ns", exporter=telemetry.EXPORTER_INHERIT)
    assert tracer is not None
    assert telemetry._state.configured is True


def test_build_scoped_tracer_routes_to_dedicated_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-inherit exporter builds a dedicated provider that receives the spans."""
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(telemetry.exporters, "_build_span_exporter", lambda _token: exporter)
    _reset()
    tracer = telemetry.build_scoped_tracer("harness.test", exporter=telemetry.EXPORTER_CONSOLE)
    with tracer.start_as_current_span("harness.span"):
        pass
    assert [span.name for span in exporter.get_finished_spans()] == ["harness.span"]


def test_build_scoped_tracer_caches_by_namespace_and_exporter() -> None:
    """Repeated calls reuse one provider/tracer instead of leaking a new one."""
    _reset()
    first = telemetry.build_scoped_tracer("harness.cache", exporter=telemetry.EXPORTER_CONSOLE)
    second = telemetry.build_scoped_tracer("harness.cache", exporter=telemetry.EXPORTER_CONSOLE)
    assert first is second


def test_importing_app_does_not_configure_telemetry_at_import() -> None:
    """Regression: importing the app must NOT configure telemetry at import time.

    A module-level ``get_tracer()`` in an import-chain module would auto-call
    ``configure_telemetry()`` with defaults, making the FastAPI lifespan's
    configured exporter / log format a silent no-op. Run in a fresh interpreter
    so an already-configured in-process singleton can't mask the regression.
    """
    code = (
        "import mangomas.telemetry as t;"
        "import mangomas.api.app;"
        "assert t._state.configured is False, 'telemetry configured at import time'"
    )
    result = subprocess.run(  # noqa: S603 -- trusted: fixed code string + sys.executable
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.gcp_trace
def test_gcp_trace_exporter_constructs_with_real_sdk() -> None:
    """Gated: build the real Cloud Trace exporter (requires the gcp extra + ADC)."""
    _reset()
    telemetry.configure_telemetry(service_name="t", exporter=telemetry.EXPORTER_GCP)
    assert telemetry._state.configured is True


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
