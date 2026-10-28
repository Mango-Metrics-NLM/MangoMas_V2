"""Tests for the /ready endpoint and readiness probe helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mangomas.adapters.llm.base import LLMClient
from mangomas.adapters.storage.base import TurnRepository
from mangomas.agents import ChatAgent
from mangomas.api.app import create_app
from mangomas.api.health import CheckResult, ReadinessReport, check_ready
from mangomas.config import get_settings
from mangomas.core import AgentContext, Orchestrator
from tests.constants import DEFAULT_API_READY_TIMEOUT
from tests.fakes import FakeLLM, FakeRepository, NonPingableFakeLLM

# Test-scoped timing: a ping that outlasts the budget by a wide margin, and a
# budget short enough that the suite never waits for the ping itself.
_SLOW_PING_SECONDS = 0.5
_TIGHT_BUDGET_SECONDS = 0.02

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_orch(
    llm: LLMClient | None = None,
    repo: TurnRepository | None = None,
) -> Orchestrator:
    fake_llm = llm if llm is not None else FakeLLM()
    ctx = AgentContext(llm=fake_llm, repo=repo)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    return orch


# ── /health (sanity) ──────────────────────────────────────────────────────────


def test_health_returns_200() -> None:
    orch = _make_orch()
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_returns_same_payload_as_health() -> None:
    orch = _make_orch()
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        health = client.get("/health")
        healthz = client.get("/healthz")
    assert health.status_code == healthz.status_code == 200
    assert health.json() == healthz.json() == {"status": "ok"}


# ── /ready route ──────────────────────────────────────────────────────────────


def test_ready_with_pingable_llm_ok(fake_repo: FakeRepository) -> None:
    orch = _make_orch(repo=fake_repo)
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    statuses = {c["name"]: c["status"] for c in body["checks"]}
    assert statuses["llm"] == "ok"
    assert statuses["db"] == "ok"


def test_readyz_returns_same_payload_as_ready(fake_repo: FakeRepository) -> None:
    orch = _make_orch(repo=fake_repo)
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        ready = client.get("/ready")
        readyz = client.get("/readyz")
    assert ready.status_code == readyz.status_code == 200
    assert ready.json() == readyz.json()


def test_ready_failing_llm_returns_503(
    fake_repo: FakeRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_llm = FakeLLM(ping_error=RuntimeError("no connection"))
    orch = _make_orch(llm=bad_llm, repo=fake_repo)
    app = create_app(orchestrator=orch)
    with caplog.at_level(logging.WARNING, logger="mangomas.api.health"), TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    statuses = {c["name"]: c["status"] for c in body["checks"]}
    assert statuses["llm"] == "error"
    assert "LLM ping failed" in caplog.text


def test_ready_no_repo_shows_not_configured() -> None:
    orch = _make_orch(repo=None)
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    statuses = {c["name"]: c["status"] for c in body["checks"]}
    assert statuses["db"] == "not_configured"


def test_ready_non_pingable_llm_shows_unknown() -> None:
    orch = _make_orch(llm=NonPingableFakeLLM())
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    statuses = {c["name"]: c["status"] for c in body["checks"]}
    assert statuses["llm"] == "unknown"


def test_ready_db_error_returns_503(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenRepository:
        async def list_turns(self, _limit: int = 50) -> list[dict[str, Any]]:
            raise OSError("disk full")

        async def save_turn(self, *_args: object, **_kwargs: object) -> int:
            return 0

        def close(self) -> None:
            pass

    orch = _make_orch(repo=BrokenRepository())
    app = create_app(orchestrator=orch)
    with caplog.at_level(logging.WARNING, logger="mangomas.api.health"), TestClient(app) as client:
        r = client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    statuses = {c["name"]: c["status"] for c in body["checks"]}
    assert statuses["db"] == "error"
    assert "DB probe failed" in caplog.text


# ── Unit tests for domain objects ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ok", True),
        ("not_configured", True),
        ("unknown", True),
        ("error", False),
    ],
)
def test_check_result_ok_property(status: str, expected: bool) -> None:
    result = CheckResult(name="test", status=status)
    assert result.ok is expected


def test_readiness_report_as_dict_all_ok() -> None:
    report = ReadinessReport(
        checks=[
            CheckResult(name="llm", status="ok"),
            CheckResult(name="db", status="ok"),
        ]
    )
    d = report.as_dict()
    assert d["ready"] is True
    assert len(d["checks"]) == 2
    names = {c["name"] for c in d["checks"]}
    assert names == {"llm", "db"}


def test_readiness_report_as_dict_with_detail() -> None:
    report = ReadinessReport(
        checks=[
            CheckResult(name="llm", status="error", detail="timeout"),
        ]
    )
    d = report.as_dict()
    assert d["ready"] is False
    assert d["checks"][0]["detail"] == "timeout"


def test_readiness_report_as_dict_omits_empty_detail() -> None:
    report = ReadinessReport(checks=[CheckResult(name="llm", status="ok")])
    d = report.as_dict()
    assert "detail" not in d["checks"][0]


async def test_check_ready_pingable_llm() -> None:
    fake_llm = FakeLLM()
    ctx = AgentContext(llm=fake_llm, repo=None)
    orch = Orchestrator(ctx)
    report = await check_ready(orch)
    assert fake_llm.pinged is True
    assert report.ready is True


async def test_check_ready_ping_error() -> None:
    fake_llm = FakeLLM(ping_error=ConnectionError("refused"))
    ctx = AgentContext(llm=fake_llm, repo=None)
    orch = Orchestrator(ctx)
    report = await check_ready(orch)
    assert report.ready is False
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["llm"] == "error"


# ── Readiness budget (spec-0031 S4; P0-C1) ───────────────────────────────────
# Before this `check_ready` awaited `ping()` and `list_turns()` unbounded, so an
# unauthenticated /readyz was held open for the dependency's own timeout (60 s
# for the LLM client) while `MANGOMAS_API__READY_TIMEOUT_SECONDS` was read by
# nothing. Mutation (proved while writing): remove the `asyncio.wait_for` in
# `_bounded` and the first test below hangs past the budget and fails.


async def test_readiness_probe_honours_the_timeout_budget() -> None:
    slow = FakeLLM(ping_delay_seconds=_SLOW_PING_SECONDS)
    orch = Orchestrator(AgentContext(llm=slow, repo=None))
    report = await asyncio.wait_for(
        check_ready(orch, timeout_seconds=_TIGHT_BUDGET_SECONDS),
        timeout=_SLOW_PING_SECONDS / 2,  # fails loudly if the budget is not enforced
    )
    assert report.ready is False
    llm = next(c for c in report.checks if c.name == "llm")
    assert llm.status == "error"
    assert "timed out after" in llm.detail


async def test_readiness_probe_without_a_budget_stays_unbounded() -> None:
    """``timeout_seconds=None`` is the pre-budget contract, kept for direct callers."""
    slow = FakeLLM(ping_delay_seconds=_TIGHT_BUDGET_SECONDS)
    orch = Orchestrator(AgentContext(llm=slow, repo=None))
    report = await check_ready(orch)
    assert report.ready is True


def test_readyz_route_passes_the_configured_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route reads the budget from ``app.state`` (set by ``create_app`` from
    settings), never from ``get_settings()`` inside health.py."""
    monkeypatch.setenv("MANGOMAS_API__READY_TIMEOUT_SECONDS", str(_TIGHT_BUDGET_SECONDS))
    get_settings.cache_clear()
    try:
        slow = FakeLLM(ping_delay_seconds=_SLOW_PING_SECONDS)
        app = create_app(orchestrator=_make_orch(llm=slow, repo=FakeRepository()))
        assert app.state.ready_timeout_seconds == _TIGHT_BUDGET_SECONDS
        with TestClient(app) as client:
            r = client.get("/readyz")
    finally:
        get_settings.cache_clear()
    assert r.status_code == 503
    statuses = {c["name"]: c for c in r.json()["checks"]}
    assert statuses["llm"]["status"] == "error"
    assert "timed out after" in statuses["llm"]["detail"]
    assert statuses["db"]["status"] == "ok"  # the budget is per probe, not shared


def test_default_budget_reaches_app_state() -> None:
    app = create_app(orchestrator=_make_orch())
    assert app.state.ready_timeout_seconds == DEFAULT_API_READY_TIMEOUT
