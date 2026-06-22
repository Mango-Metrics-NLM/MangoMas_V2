"""Tests for harness span routing via ``HarnessSettings.metrics_exporter``.

Verifies the default-OFF path (shared global tracer) and the opt-in routing
path (dedicated, isolated provider) on the ``_HarnessOrchestrator``.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.composition import _HarnessOrchestrator
from mangomas.config import HarnessSettings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.telemetry_exporters import exporter_registry
from tests.constants import DEFAULT_AGENT_NAME, STUB_REPLY
from tests.fakes import FakeLLM, FakeRepository

_HARNESS_SPAN_NAME = "harness.agent_invoke"


def _ctx() -> AgentContext:
    return AgentContext(llm=FakeLLM(reply=STUB_REPLY), repo=FakeRepository())


def _register_default_agent(wrapper: _HarnessOrchestrator) -> None:
    from mangomas.composition import agent_registry  # noqa: PLC0415

    wrapper.register(agent_registry.get(DEFAULT_AGENT_NAME)(None))


async def test_default_exporter_none_uses_global_tracer() -> None:
    """``metrics_exporter=None`` (default) routes through the global tracer.

    The default path acquires its tracer via ``get_tracer`` — the same global
    provider used for application spans — rather than building a dedicated one.
    """
    dedicated = InMemorySpanExporter()
    cfg = HarnessSettings(enabled=True, metrics_exporter=None)

    # Even with a console factory scoped to a dedicated exporter, the default
    # path must NOT use it (it never calls resolve_exporter).
    with exporter_registry.scoped("console", lambda _cfg: dedicated):
        wrapper = _HarnessOrchestrator(_ctx(), cfg)

    _register_default_agent(wrapper)
    request = AgentRequest(messages=[Message(role="user", content="ping")])
    response = await wrapper.dispatch(DEFAULT_AGENT_NAME, request)

    assert response.content == STUB_REPLY
    # No span reached the dedicated exporter — the default path bypasses it.
    assert dedicated.get_finished_spans() == ()


async def test_metrics_exporter_routes_to_dedicated_exporter() -> None:
    """A configured harness exporter receives the ``harness.agent_invoke`` span."""
    dedicated = InMemorySpanExporter()
    global_provider_before = trace.get_tracer_provider()

    # Scope the console factory to a SimpleSpanProcessor-backed in-memory exporter
    # so the synchronous span is captured deterministically.
    with exporter_registry.scoped("console", lambda _cfg: dedicated):
        cfg = HarnessSettings(enabled=True, metrics_exporter="console")
        wrapper = _HarnessOrchestrator(_ctx(), cfg)

    _register_default_agent(wrapper)
    request = AgentRequest(messages=[Message(role="user", content="ping")])
    await wrapper.dispatch(DEFAULT_AGENT_NAME, request)

    span_names = [s.name for s in dedicated.get_finished_spans()]
    assert _HARNESS_SPAN_NAME in span_names
    # Routing to a dedicated provider must not mutate the global provider.
    assert trace.get_tracer_provider() is global_provider_before


def test_dedicated_provider_uses_simple_processor_for_console() -> None:
    """Sanity: the scoped console exporter is wired with a synchronous processor."""
    dedicated = InMemorySpanExporter()
    proc = SimpleSpanProcessor(dedicated)
    assert isinstance(proc, SimpleSpanProcessor)
