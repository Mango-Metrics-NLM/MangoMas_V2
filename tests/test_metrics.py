"""Tests for the opt-in metrics pipeline (spec 0009 / ADR-0013).

``set_meter_provider`` is process-global and one-shot, so a module-scoped fixture
installs the single real ``MeterProvider`` (backed by an ``InMemoryMetricReader``)
for the record-helper + API-boundary assertions; the ``configure_metrics`` branch
coverage uses monkeypatched provider installation to stay independent of it.
"""

from __future__ import annotations

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
    reader = InMemoryMetricReader()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(telemetry, "_build_metric_reader", lambda _tok: reader)
    # telemetry uses the same module object, so patching it here patches the call.
    monkeypatch.setattr(otel_metrics, "set_meter_provider", lambda p: captured.setdefault("p", p))
    telemetry._state.metrics_configured = False
    telemetry.configure_metrics(enabled=True)  # reader=None → else branch builds one
    assert "p" in captured
    telemetry._state.metrics_configured = False


def test_build_metric_reader_console() -> None:
    reader = telemetry._build_metric_reader(telemetry.EXPORTER_CONSOLE)
    assert isinstance(reader, PeriodicExportingMetricReader)


def test_build_metric_reader_unknown_raises() -> None:
    with pytest.raises(ConfigError):
        telemetry._build_metric_reader("bogus")


def test_build_metric_reader_gcp_uses_lazy_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "_lazy_cloud_monitoring_exporter", ConsoleMetricExporter)
    assert telemetry._build_metric_reader(telemetry.EXPORTER_GCP) is not None
