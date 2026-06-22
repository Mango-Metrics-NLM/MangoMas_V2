"""Unit tests for :mod:`mangomas.telemetry_exporters`.

The ``otlp`` / ``gcp`` factories lazy-import optional SDKs. To exercise the
*selection* logic without those extras installed we substitute the lazily
imported exporter modules via ``monkeypatch.setitem(sys.modules, ...)`` — the
same synthetic-module trick used in ``tests/test_secrets_gcp.py``. The real
SDK construction lines stay under ``# pragma: no cover``.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.config import HarnessSettings, TelemetrySettings
from mangomas.telemetry_exporters import (
    build_harness_provider,
    exporter_registry,
    make_span_processor,
    resolve_exporter,
)

# ── Stubbed exporter SDKs ─────────────────────────────────────────────────────


class _FakeOTLPSpanExporter:
    """Stand-in for the OTLP/gRPC exporter."""

    def __init__(self, *, endpoint: str) -> None:
        self.endpoint = endpoint


class _FakeCloudTraceSpanExporter:
    """Stand-in for the Cloud Trace exporter."""

    def __init__(self, *, project_id: str) -> None:
        self.project_id = project_id


@pytest.fixture
def _stub_otlp_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substitute ``opentelemetry.exporter.otlp...trace_exporter``."""
    mod = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    mod.OTLPSpanExporter = _FakeOTLPSpanExporter  # type: ignore[attr-defined]
    for pkg in (
        "opentelemetry.exporter",
        "opentelemetry.exporter.otlp",
        "opentelemetry.exporter.otlp.proto",
        "opentelemetry.exporter.otlp.proto.grpc",
    ):
        if pkg not in sys.modules:
            monkeypatch.setitem(sys.modules, pkg, types.ModuleType(pkg))
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.grpc.trace_exporter", mod)


@pytest.fixture
def _stub_gcp_trace_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substitute ``opentelemetry.exporter.cloud_trace``."""
    mod = types.ModuleType("opentelemetry.exporter.cloud_trace")
    mod.CloudTraceSpanExporter = _FakeCloudTraceSpanExporter  # type: ignore[attr-defined]
    if "opentelemetry.exporter" not in sys.modules:
        monkeypatch.setitem(
            sys.modules, "opentelemetry.exporter", types.ModuleType("opentelemetry.exporter")
        )
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.cloud_trace", mod)


@pytest.fixture(autouse=True)
def _restore_registry() -> Iterator[None]:
    """Reset the exporter registry to its eager-only baseline after each test.

    Lazy-registration of ``otlp``/``gcp`` mutates the module-level registry;
    restoring keeps tests independent of execution order.
    """
    yield
    for name in ("otlp", "gcp"):
        # ``Registry`` has no public delete; reach into the store under its lock.
        with exporter_registry._lock:
            exporter_registry._store.pop(name, None)


# ── console (eager) ───────────────────────────────────────────────────────────


def test_console_registered_eagerly() -> None:
    assert "console" in exporter_registry.available()


def test_resolve_console_exporter() -> None:
    exporter = resolve_exporter(TelemetrySettings(exporter="console"))
    assert isinstance(exporter, ConsoleSpanExporter)


# ── otlp (lazy) ───────────────────────────────────────────────────────────────


def test_resolve_otlp_lazy_registers_and_builds(_stub_otlp_sdk: None) -> None:
    cfg = TelemetrySettings(exporter="otlp", otlp_endpoint="http://collector:4317")
    exporter = resolve_exporter(cfg)
    assert isinstance(exporter, _FakeOTLPSpanExporter)
    assert exporter.endpoint == "http://collector:4317"
    # The factory is now registered for reuse.
    assert "otlp" in exporter_registry.available()


# ── gcp (lazy) ────────────────────────────────────────────────────────────────


def test_resolve_gcp_lazy_registers_and_builds(_stub_gcp_trace_sdk: None) -> None:
    cfg = TelemetrySettings(exporter="gcp", gcp_project_id="proj-123")
    exporter = resolve_exporter(cfg)
    assert isinstance(exporter, _FakeCloudTraceSpanExporter)
    assert exporter.project_id == "proj-123"
    assert "gcp" in exporter_registry.available()


# ── processor selection ───────────────────────────────────────────────────────


def test_console_uses_simple_processor() -> None:
    proc = make_span_processor(ConsoleSpanExporter(), exporter_name="console")
    assert isinstance(proc, SimpleSpanProcessor)


def test_non_console_uses_batch_processor() -> None:
    proc = make_span_processor(InMemorySpanExporter(), exporter_name="otlp")
    assert isinstance(proc, BatchSpanProcessor)


# ── build_harness_provider ────────────────────────────────────────────────────


def test_build_harness_provider_returns_isolated_provider() -> None:
    """A console harness exporter yields a usable, isolated provider."""
    cfg = HarnessSettings(enabled=True, metrics_exporter="console")
    provider = build_harness_provider(cfg)
    try:
        tracer = provider.get_tracer(cfg.metrics_namespace)
        with tracer.start_as_current_span("harness-span") as span:
            span.set_attribute("k", "v")
        assert provider is not None
    finally:
        provider.shutdown()
