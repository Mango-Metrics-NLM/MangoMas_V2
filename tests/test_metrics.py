"""Tests for the opt-in metrics pipeline (spec 0009 / ADR-0013).

``set_meter_provider`` is process-global and one-shot, so a module-scoped fixture
installs the single real ``MeterProvider`` (backed by an ``InMemoryMetricReader``)
for the record-helper + API-boundary assertions; the ``configure_metrics`` branch
coverage uses monkeypatched provider installation to stay independent of it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
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
from mangomas.agents import ChatAgent
from mangomas.api import app as app_module
from mangomas.api.app import create_app
from mangomas.cli import _runtime as cli_runtime
from mangomas.config import LoopSettings, get_settings
from mangomas.core import AgentContext, AgentRequest, AgentResponse, Message, Orchestrator
from mangomas.errors import AgentNotFound, ConfigError, LLMUnavailable, StepTimeout
from tests.constants import SLOW_AGENT_DELAY_SECONDS, TINY_STEP_TIMEOUT_SECONDS
from tests.fakes import FakeLLM


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


# ── Orchestrator-seam emission (spec-0026 / ADR-0026) ─────────────────────────
# Metric emission lives inside Orchestrator.dispatch/_stream_agent, so direct
# (CLI-style) dispatch counts — no HTTP involved. Agent names are unique per
# test because the module-scoped reader is cumulative.


class _NamedAgent:
    """Minimal agent with an injectable name (unique per metric test)."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def handle(self, request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        return AgentResponse(content=f"n:{len(request.messages)}", agent=self.name)


class _SlowMetricsAgent:
    name = "slow-metrics-agent"

    async def handle(self, _request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        await asyncio.sleep(SLOW_AGENT_DELAY_SECONDS)
        return AgentResponse(content="late", agent=self.name)


def _direct_orch(*agents: Any, loop_settings: LoopSettings | None = None) -> Orchestrator:
    orch = Orchestrator(AgentContext(llm=FakeLLM(), repo=None), loop_settings=loop_settings)
    for agent in agents:
        orch.register(agent)
    return orch


_DIRECT_REQUEST = AgentRequest(messages=[Message(role="user", content="hi")])


async def test_direct_dispatch_records_ok_and_duration(
    metric_reader: InMemoryMetricReader,
) -> None:
    """CLI-style dispatch (no HTTP route anywhere) emits ok + duration points."""
    orch = _direct_orch(_NamedAgent("direct-ok-agent"))
    await orch.dispatch("direct-ok-agent", _DIRECT_REQUEST)
    inv = _point_for(
        metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": "direct-ok-agent", "status": "ok"}
    )
    assert inv is not None
    assert inv.value == 1
    dur = _point_for(metric_reader, app_metrics.AGENT_DURATION, {"agent": "direct-ok-agent"})
    assert dur is not None
    assert dur.count == 1


async def test_direct_dispatch_records_error_metrics(
    metric_reader: InMemoryMetricReader,
) -> None:
    orch = _direct_orch()
    with pytest.raises(AgentNotFound):
        await orch.dispatch("direct-ghost-agent", _DIRECT_REQUEST)
    inv = _point_for(
        metric_reader,
        app_metrics.AGENT_INVOCATIONS,
        {"agent": "direct-ghost-agent", "status": "error"},
    )
    assert inv is not None
    assert inv.value == 1
    err = _point_for(
        metric_reader,
        app_metrics.AGENT_ERRORS,
        {"agent": "direct-ghost-agent", "code": "agent_not_found"},
    )
    assert err is not None
    assert err.value == 1


async def test_step_timeout_records_error_metric(metric_reader: InMemoryMetricReader) -> None:
    """A StepTimeout surfaces on the error counter with its own code."""
    orch = _direct_orch(
        _SlowMetricsAgent(),
        loop_settings=LoopSettings(step_timeout_seconds=TINY_STEP_TIMEOUT_SECONDS),
    )
    with pytest.raises(StepTimeout):
        await orch.dispatch("slow-metrics-agent", _DIRECT_REQUEST)
    err = _point_for(
        metric_reader,
        app_metrics.AGENT_ERRORS,
        {"agent": "slow-metrics-agent", "code": "step_timeout"},
    )
    assert err is not None
    assert err.value == 1


async def test_pipeline_counts_each_inner_step(metric_reader: InMemoryMetricReader) -> None:
    """dispatch_pipeline delegates through dispatch: each hop is its own point."""
    orch = _direct_orch(_NamedAgent("pipe-agent-a"), _NamedAgent("pipe-agent-b"))
    await orch.dispatch_pipeline(["pipe-agent-a", "pipe-agent-b"], _DIRECT_REQUEST)
    for agent in ("pipe-agent-a", "pipe-agent-b"):
        inv = _point_for(
            metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": agent, "status": "ok"}
        )
        assert inv is not None, agent
        assert inv.value == 1


async def test_fan_out_counts_each_inner_step(metric_reader: InMemoryMetricReader) -> None:
    orch = _direct_orch(_NamedAgent("fan-agent-a"), _NamedAgent("fan-agent-b"))
    await orch.dispatch_fan_out(["fan-agent-a", "fan-agent-b"], _DIRECT_REQUEST)
    for agent in ("fan-agent-a", "fan-agent-b"):
        inv = _point_for(
            metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": agent, "status": "ok"}
        )
        assert inv is not None, agent
        assert inv.value == 1


async def test_fan_out_settled_counts_each_inner_step_including_failures(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Settled fan-out (spec-0027) adds no instrument of its own: each inner
    dispatch — including the failed one — records its own per-agent point."""
    orch = _direct_orch(_NamedAgent("settled-ok-agent"))
    outcomes = await orch.dispatch_fan_out_settled(
        ["settled-ok-agent", "settled-ghost-agent"], _DIRECT_REQUEST
    )
    assert [outcome.ok for outcome in outcomes] == [True, False]

    inv_ok = _point_for(
        metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": "settled-ok-agent", "status": "ok"}
    )
    assert inv_ok is not None
    assert inv_ok.value == 1
    inv_err = _point_for(
        metric_reader,
        app_metrics.AGENT_INVOCATIONS,
        {"agent": "settled-ghost-agent", "status": "error"},
    )
    assert inv_err is not None
    assert inv_err.value == 1
    err = _point_for(
        metric_reader,
        app_metrics.AGENT_ERRORS,
        {"agent": "settled-ghost-agent", "code": "agent_not_found"},
    )
    assert err is not None
    assert err.value == 1


def test_route_invoke_counts_exactly_once(metric_reader: InMemoryMetricReader) -> None:
    """One HTTP invoke = exactly one invocation point (no route double-count).

    Mutation proof (spec-0026): re-adding record_agent_invocation to the
    invoke handler makes this fail with value == 2.
    """
    orch = _direct_orch(_NamedAgent("once-invoke-agent"))
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        r = client.post(
            "/agents/once-invoke-agent/invoke",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
    inv = _point_for(
        metric_reader,
        app_metrics.AGENT_INVOCATIONS,
        {"agent": "once-invoke-agent", "status": "ok"},
    )
    assert inv is not None
    assert inv.value == 1
    dur = _point_for(metric_reader, app_metrics.AGENT_DURATION, {"agent": "once-invoke-agent"})
    assert dur is not None
    assert dur.count == 1


def test_route_stream_counts_exactly_once(metric_reader: InMemoryMetricReader) -> None:
    """One fully-drained HTTP stream = exactly one ok invocation point."""
    orch = _direct_orch(_NamedAgent("once-stream-agent"))
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        r = client.post("/agents/once-stream-agent/stream", json=_STREAM_BODY)
        assert r.status_code == 200
    inv = _point_for(
        metric_reader,
        app_metrics.AGENT_INVOCATIONS,
        {"agent": "once-stream-agent", "status": "ok"},
    )
    assert inv is not None
    assert inv.value == 1


# ── API boundary: HTTP end-to-end (orchestrator-seam emission via a route) ────
# These prove the orchestrator emission is reached through a real route call —
# removing the emission from Orchestrator.dispatch fails them, since the route
# itself no longer records anything (spec-0026 mutation proof #1).


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


# ── API boundary emission: /agents/{name}/stream (spec-0025 / ADR-0025) ───────


class _StreamMetricsAgent:
    """Non-streaming agent with a unique name so its metric points are
    unambiguous under the module-scoped (cumulative) reader."""

    name = "stream-unit-agent"

    async def handle(self, request: AgentRequest, _ctx: AgentContext) -> AgentResponse:
        return AgentResponse(content=f"s:{len(request.messages)}", agent=self.name)


_STREAM_BODY = {"messages": [{"role": "user", "content": "hi"}]}


def test_stream_records_ok_metrics_on_full_drain(metric_reader: InMemoryMetricReader) -> None:
    ctx = AgentContext(llm=FakeLLM(), repo=None)
    orch = Orchestrator(ctx)
    orch.register(_StreamMetricsAgent())
    app = create_app(orchestrator=orch)
    with TestClient(app) as client:
        r = client.post("/agents/stream-unit-agent/stream", json=_STREAM_BODY)
        assert r.status_code == 200
    inv = _point_for(
        metric_reader,
        app_metrics.AGENT_INVOCATIONS,
        {"agent": "stream-unit-agent", "status": "ok"},
    )
    assert inv is not None
    assert inv.value >= 1
    dur = _point_for(metric_reader, app_metrics.AGENT_DURATION, {"agent": "stream-unit-agent"})
    assert dur is not None
    assert dur.count >= 1


def test_stream_records_error_metrics_pre_stream(
    metric_reader: InMemoryMetricReader, orchestrator: Orchestrator
) -> None:
    """AgentNotFound before streaming begins mirrors invoke's error metrics."""
    app = create_app(orchestrator=orchestrator)
    with TestClient(app) as client:
        r = client.post("/agents/ghost-stream/stream", json=_STREAM_BODY)
        assert r.status_code == 404
    inv = _point_for(
        metric_reader,
        app_metrics.AGENT_INVOCATIONS,
        {"agent": "ghost-stream", "status": "error"},
    )
    assert inv is not None
    assert inv.value >= 1
    err = _point_for(
        metric_reader,
        app_metrics.AGENT_ERRORS,
        {"agent": "ghost-stream", "code": "agent_not_found"},
    )
    assert err is not None
    assert err.value >= 1


def test_stream_records_error_metrics_mid_drain(metric_reader: InMemoryMetricReader) -> None:
    """A MangomasError raised mid-drain records error metrics before re-raising.

    The 200 + text/event-stream headers are already sent, so no error envelope
    can run — the SSE stream just ends (spec-0022 R14) and the re-raised
    exception surfaces through TestClient instead of a response.
    """
    llm = FakeLLM(chunks=["a", "b"], raise_on_stream=LLMUnavailable("gone"), raise_after_chunks=1)
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    app = create_app(orchestrator=orch)
    with TestClient(app) as client, contextlib.suppress(Exception):
        client.post("/agents/chat/stream", json=_STREAM_BODY)
    inv = _point_for(
        metric_reader, app_metrics.AGENT_INVOCATIONS, {"agent": "chat", "status": "error"}
    )
    assert inv is not None
    assert inv.value >= 1
    err = _point_for(
        metric_reader, app_metrics.AGENT_ERRORS, {"agent": "chat", "code": "llm_unavailable"}
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


# ── CLI entry point: bootstrap parity with the HTTP lifespan ──────────────────
# `Orchestrator.dispatch` records unconditionally (spec-0026 / ADR-0026) on the
# premise that "every dispatch path — HTTP, CLI, workflow nodes" reaches an
# installed MeterProvider. The CLI half of that premise was false: the CLI
# bootstrap called `configure_telemetry` and nothing else, so with
# MANGOMAS_TELEMETRY__METRICS_ENABLED=true the global provider stayed the OTel
# no-op proxy and every metric from `mangomas chat|eval|workflow run` was
# silently dropped. `configure_metrics` had exactly one call site in `src/`.
#
# Two levels, both directions each: the subprocess pair asserts the *effect*
# (a real provider is installed / is not), the spy pair asserts the *call* is
# byte-identical to the one in `api/app.py::_lifespan`.

_METRICS_ENABLED_ENV = "MANGOMAS_TELEMETRY__METRICS_ENABLED"
_PROBE_PREFIX = "CLI_METRICS_PROBE:"

# Run in a fresh interpreter: `set_meter_provider` is process-global and
# one-shot, and this module's `metric_reader` fixture has already spent it.
# In-process this could only ever observe that earlier provider — green
# whether or not the CLI configured anything, the exact silent pass this test
# exists to remove.
_PROBE_CODE = (
    "import json;"
    "from mangomas.cli._runtime import configure_cli_logging;"
    "configure_cli_logging();"
    "from opentelemetry import metrics as _otel_metrics;"
    "from opentelemetry.sdk.metrics import MeterProvider as _SdkMeterProvider;"
    "from mangomas.telemetry import _state;"
    "_provider = _otel_metrics.get_meter_provider();"
    f"print('{_PROBE_PREFIX}' + json.dumps({{"
    "'configured': _state.metrics_configured,"
    "'real_provider': isinstance(_provider, _SdkMeterProvider),"
    "'provider': type(_provider).__name__}))"
)


def _cli_bootstrap_probe(metrics_env: str | None) -> dict[str, Any]:
    """Bootstrap the CLI runtime in a subprocess; report the installed provider."""
    env = {**os.environ}
    if metrics_env is None:
        env.pop(_METRICS_ENABLED_ENV, None)  # the documented default: unset
    else:
        env[_METRICS_ENABLED_ENV] = metrics_env
    result = subprocess.run(  # noqa: S603 -- trusted: fixed code string + sys.executable
        [sys.executable, "-c", _PROBE_CODE],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    # Prefix-matched, not last-line: an enabled run installs a
    # PeriodicExportingMetricReader whose ConsoleMetricExporter may flush to
    # stdout at exit.
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith(_PROBE_PREFIX)]
    assert lines, f"probe line missing from stdout:\n{result.stdout}\n{result.stderr}"
    parsed: dict[str, Any] = json.loads(lines[-1][len(_PROBE_PREFIX) :])
    return parsed


@pytest.mark.parametrize(
    ("metrics_env", "expect_installed"),
    [("true", True), (None, False)],
    ids=["metrics-enabled", "default-off"],
)
def test_cli_bootstrap_installs_a_meter_provider_only_when_enabled(
    metrics_env: str | None, expect_installed: bool
) -> None:
    """`configure_cli_logging` must engage metrics on the HTTP app's terms.

    Mutation proof: drop the `configure_metrics` call from
    `cli/_runtime.configure_cli_logging` and the `metrics-enabled` case fails
    with `configured False / provider _ProxyMeterProvider` — the state the bug
    report measured. The `default-off` case passes either way by design: it is
    the guard that the fix did not turn the opt-in pipeline on for everyone.
    """
    probe = _cli_bootstrap_probe(metrics_env)
    assert probe["configured"] is expect_installed, (
        f"telemetry._state.metrics_configured={probe['configured']} with "
        f"{_METRICS_ENABLED_ENV}={metrics_env!r}"
    )
    assert probe["real_provider"] is expect_installed, (
        f"global MeterProvider is {probe['provider']!r} with "
        f"{_METRICS_ENABLED_ENV}={metrics_env!r}; CLI dispatch records into it"
    )


@pytest.fixture
def _restore_root_log_level() -> Iterator[None]:
    """Undo `configure_cli_logging`'s process-global root-level change.

    Setting the root level is the CLI bootstrap's actual job, so a test that
    calls it leaks one — and a leaked level silently changes `caplog` capture
    in whatever test file runs next, failing there and passing in isolation.
    """
    root = logging.getLogger()
    previous = root.level
    yield
    root.setLevel(previous)


@pytest.mark.usefixtures("_restore_root_log_level")
@pytest.mark.parametrize(
    ("metrics_env", "expected_enabled"),
    [("true", True), (None, False)],
    ids=["metrics-enabled", "default-off"],
)
def test_cli_bootstrap_mirrors_the_lifespan_metrics_call(
    monkeypatch: pytest.MonkeyPatch, metrics_env: str | None, expected_enabled: bool
) -> None:
    """Same settings source, same kwargs, same conditionality as `_lifespan`.

    Equality on the whole captured mapping rather than a membership check: it
    is what pins *parity* with `api/app.py`, which passes both arguments by
    keyword off one `Settings` object. A positional call, a hard-coded exporter
    token, or an extra argument all fail here.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli_runtime, "configure_telemetry", lambda **_kw: None)
    monkeypatch.setattr(cli_runtime, "configure_metrics", lambda **kw: captured.update(kw))
    if metrics_env is None:
        monkeypatch.delenv(_METRICS_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(_METRICS_ENABLED_ENV, metrics_env)
    get_settings.cache_clear()

    cli_runtime.configure_cli_logging()

    telemetry_cfg = get_settings().telemetry
    assert captured == {"exporter": telemetry_cfg.exporter, "enabled": expected_enabled}
    assert telemetry_cfg.metrics_enabled is expected_enabled  # the settings source itself


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
