"""Tests for the opt-in metrics pipeline (spec 0009 / ADR-0013).

``set_meter_provider`` is process-global and one-shot, so a module-scoped fixture
installs the single real ``MeterProvider`` (backed by an ``InMemoryMetricReader``)
for the record-helper + API-boundary assertions; the ``configure_metrics`` branch
coverage uses monkeypatched provider installation to stay independent of it.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    InMemoryMetricReader,
    PeriodicExportingMetricReader,
)

from mangomas import metrics as app_metrics
from mangomas import telemetry
from mangomas.api import app as app_module
from mangomas.api.app import create_app
from mangomas.config import get_settings
from mangomas.core import Orchestrator
from mangomas.errors import ConfigError


def _points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    out: list[Any] = []
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    out.extend(metric.data.data_points)
    return out


def _point_for(reader: InMemoryMetricReader, name: str, attrs: dict[str, str]) -> Any:
    for point in _points(reader, name):
        if all(point.attributes.get(key) == value for key, value in attrs.items()):
            return point
    return None


@pytest.fixture(scope="module")
def metric_reader() -> InMemoryMetricReader:
    """Install the single real MeterProvider (set_meter_provider is process-global)."""
    reader = InMemoryMetricReader()
    telemetry._state.metrics_configured = False
    telemetry.configure_metrics(enabled=True, reader=reader)
    app_metrics._state.instruments = None  # rebind instruments to the new provider
    return reader


# ── record helpers ────────────────────────────────────────────────────────────


def test_record_helpers_emit_points(metric_reader: InMemoryMetricReader) -> None:
    app_metrics.record_agent_invocation("unit-agent", "ok")
    app_metrics.record_agent_error("unit-agent", "some_code")
    app_metrics.record_agent_duration("unit-agent", 0.01)

    inv = _point_for(
        metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": "unit-agent", "status": "ok"}
    )
    assert inv is not None
    assert inv.value == 1
    err = _point_for(
        metric_reader, app_metrics.AGENT_ERRORS, {"agent": "unit-agent", "code": "some_code"}
    )
    assert err is not None
    assert err.value == 1
    dur = _point_for(metric_reader, app_metrics.AGENT_DURATION, {"agent": "unit-agent"})
    assert dur is not None
    assert dur.count == 1


# ── API boundary emission ─────────────────────────────────────────────────────


def test_invoke_records_ok_metrics(
    metric_reader: InMemoryMetricReader, orchestrator: Orchestrator
) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            "/agents/chat/invoke", json={"messages": [{"role": "user", "content": "hi"}]}
        )
        assert r.status_code == 200
    inv = _point_for(
        metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": "chat", "status": "ok"}
    )
    assert inv is not None
    assert inv.value >= 1
    dur = _point_for(metric_reader, app_metrics.AGENT_DURATION, {"agent": "chat"})
    assert dur is not None
    assert dur.count >= 1


def test_invoke_records_error_metrics(
    metric_reader: InMemoryMetricReader, orchestrator: Orchestrator
) -> None:
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post(
            "/agents/ghost-agent/invoke", json={"messages": [{"role": "user", "content": "hi"}]}
        )
        assert r.status_code == 404
    inv = _point_for(
        metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": "ghost-agent", "status": "error"}
    )
    assert inv is not None
    assert inv.value >= 1
    err = _point_for(
        metric_reader,
        app_metrics.AGENT_ERRORS,
        {"agent": "ghost-agent", "code": "agent_not_found"},
    )
    assert err is not None
    assert err.value >= 1


# ── telemetry.configure_metrics / _build_metric_reader ────────────────────────


def test_lifespan_engages_metrics_when_enabled(
    monkeypatch: pytest.MonkeyPatch, orchestrator: Orchestrator
) -> None:
    """MANGOMAS_TELEMETRY__METRICS_ENABLED=true flows through the app lifespan
    into configure_metrics(enabled=True) — proven via a spy, without touching the
    process-global provider."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(app_module, "build_orchestrator", lambda _settings: orchestrator)
    monkeypatch.setattr(app_module, "configure_telemetry", lambda **_kw: None)
    monkeypatch.setattr(app_module, "configure_metrics", lambda **kw: captured.update(kw))
    monkeypatch.setenv("MANGOMAS_TELEMETRY__METRICS_ENABLED", "true")
    get_settings.cache_clear()

    fastapi_app = create_app()  # no injected orchestrator → runs _lifespan
    with TestClient(fastapi_app):
        pass
    assert captured.get("enabled") is True


def test_configure_metrics_disabled_is_noop() -> None:
    telemetry.configure_metrics(enabled=False)
    assert telemetry.get_meter("x") is not None


def test_configure_metrics_idempotent() -> None:
    telemetry._state.metrics_configured = True
    telemetry.configure_metrics(enabled=True, reader=InMemoryMetricReader())  # early return
    assert telemetry._state.metrics_configured is True


def test_configure_metrics_builds_reader_from_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exporter token reaches `_build_metric_reader`, whose reader is installed.

    Asserts the patched builder was **called**, not merely that some provider
    was installed. The weaker form (`assert "p" in captured`) passed whether or
    not the patch landed: `configure_metrics` installs a provider either way,
    so a broken seam left the test green while quietly constructing a real
    `PeriodicExportingMetricReader(ConsoleMetricExporter())` — spawning a
    background export thread that prints metrics to stdout for the rest of the
    run.

    That matters most for the spec-0015 split: once `_build_metric_reader`
    moves to `telemetry/exporters.py` and this caller to `telemetry/meters.py`,
    a facade preserves the function's identity but not the caller's name
    binding. Recording the call is what makes that failure loud.

    Call recording is deliberately used in preference to inspecting the
    provider's readers — `MeterProvider._metric_readers` is private SDK state
    that could be renamed by an OTel upgrade, whereas "our builder ran, with
    our token" is the seam's actual contract.
    """
    reader = InMemoryMetricReader()
    captured: dict[str, Any] = {}
    builder_calls: list[str] = []

    def _fake_builder(token: str) -> InMemoryMetricReader:
        builder_calls.append(token)
        return reader

    monkeypatch.setattr(telemetry.exporters, "_build_metric_reader", _fake_builder)
    monkeypatch.setattr(otel_metrics, "set_meter_provider", lambda p: captured.setdefault("p", p))
    telemetry._state.metrics_configured = False

    telemetry.configure_metrics(
        enabled=True, exporter=telemetry.EXPORTER_GCP
    )  # reader=None → else branch builds one

    assert builder_calls == [telemetry.EXPORTER_GCP], (
        "the patched _build_metric_reader was not reached — configure_metrics "
        "resolved the name somewhere this patch does not cover"
    )
    assert "p" in captured
    telemetry._state.metrics_configured = False


def test_build_metric_reader_console() -> None:
    reader = telemetry._build_metric_reader(telemetry.EXPORTER_CONSOLE)
    assert isinstance(reader, PeriodicExportingMetricReader)


def test_build_metric_reader_unknown_raises() -> None:
    with pytest.raises(ConfigError):
        telemetry._build_metric_reader("bogus")


def test_build_metric_reader_gcp_uses_lazy_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        telemetry.exporters, "_lazy_cloud_monitoring_exporter", ConsoleMetricExporter
    )
    assert telemetry._build_metric_reader(telemetry.EXPORTER_GCP) is not None


# ── lazy-instrument singleton thread-safety (spec 0014 / D7) ──────────────────


def test_instruments_singleton_survives_concurrent_first_record() -> None:
    """Regression: 32 threads racing the first record must observe exactly one
    ``_Instruments`` (double-checked locking in ``metrics._instruments``)."""
    workers = 32
    app_metrics._state.instruments = None  # force re-creation under contention
    barrier = threading.Barrier(workers)

    def _get(_: int) -> app_metrics._Instruments:
        barrier.wait()  # line all workers up on the empty singleton
        return app_metrics._instruments()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_get, range(workers)))

    first = results[0]
    assert all(instruments is first for instruments in results)
    assert app_metrics._state.instruments is first
