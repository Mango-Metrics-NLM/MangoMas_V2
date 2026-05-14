"""Tests for AccessLogMiddleware."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from mangomas.api.app import create_app
from mangomas.core import Orchestrator


def test_middleware_logs_info_on_request(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every HTTP request produces an INFO log with method and path."""
    app = create_app(orchestrator=orchestrator)
    with caplog.at_level(logging.INFO, logger="mangomas.api.middleware"), TestClient(app) as client:
        client.get("/health")
    assert "GET" in caplog.text
    assert "/health" in caplog.text


def test_middleware_logs_status_code(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The INFO record includes the HTTP status code."""
    app = create_app(orchestrator=orchestrator)
    with caplog.at_level(logging.INFO, logger="mangomas.api.middleware"), TestClient(app) as client:
        client.get("/health")
    assert "200" in caplog.text


def test_middleware_logs_debug_on_arrival(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A DEBUG record is emitted immediately on request arrival."""
    app = create_app(orchestrator=orchestrator)
    with (
        caplog.at_level(logging.DEBUG, logger="mangomas.api.middleware"),
        TestClient(app) as client,
    ):
        client.get("/agents")
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("/agents" in r.getMessage() for r in debug_records)


def test_middleware_info_record_has_extra_fields(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The INFO log record carries structured extra fields for JSON consumers."""
    app = create_app(orchestrator=orchestrator)
    with caplog.at_level(logging.INFO, logger="mangomas.api.middleware"), TestClient(app) as client:
        client.get("/health")
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert info_records, "Expected at least one INFO record"
    rec = info_records[0]
    # Extra fields
    assert hasattr(rec, "request_id")
    assert hasattr(rec, "method")
    assert hasattr(rec, "path")
    assert hasattr(rec, "status_code")
    assert hasattr(rec, "latency_ms")
    assert rec.method == "GET"
    assert rec.path == "/health"
    assert rec.status_code == 200


def test_middleware_logs_404_status_code(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-2xx status codes are logged correctly."""
    app = create_app(orchestrator=orchestrator)
    with (
        caplog.at_level(logging.INFO, logger="mangomas.api.middleware"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        client.post(
            "/agents/ghost/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    status_codes = [getattr(r, "status_code", None) for r in info_records]
    assert 404 in status_codes


def test_middleware_latency_is_non_negative(
    orchestrator: Orchestrator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Latency is a non-negative float in milliseconds."""
    app = create_app(orchestrator=orchestrator)
    with caplog.at_level(logging.INFO, logger="mangomas.api.middleware"), TestClient(app) as client:
        client.get("/health")
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert info_records
    latency = getattr(info_records[0], "latency_ms", -1)
    assert isinstance(latency, float)
    assert latency >= 0
