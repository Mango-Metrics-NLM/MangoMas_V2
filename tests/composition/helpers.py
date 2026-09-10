"""Shared helpers for composition-root tests."""

from __future__ import annotations

from typing import Literal

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import mangomas.telemetry as telemetry_module
from mangomas.composition import _HarnessOrchestrator, agent_registry
from mangomas.config import Settings
from mangomas.core import Orchestrator
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.constants import DEFAULT_AGENT_NAME, STUB_REPLY
from tests.fakes import FakeLLM, FakeRepository

# Distinct namespace per streaming-span test so build_scoped_tracer's module-
# level _scoped_tracers cache (keyed by (namespace, exporter)) never returns
# another test's already-built tracer/exporter pair.
HARNESS_STREAM_SPAN_NAMESPACE: str = "test.harness.stream_span"
HARNESS_STREAM_SPAN_EXPORTER: Literal["console"] = "console"


def close_repo(orch: Orchestrator) -> None:
    repo = orch.context.repo
    assert repo is not None
    repo.close()


def make_traced_stream_wrapper(
    monkeypatch: pytest.MonkeyPatch, exporter: InMemorySpanExporter, *, namespace: str
) -> tuple[_HarnessOrchestrator, AgentRequest]:
    """Build a harness wrapper whose spans land in *exporter*, plus a request.

    *namespace* must be unique per test (a plain shared constant is not
    enough): ``build_scoped_tracer`` caches its ``TracerProvider`` in a
    module-level dict keyed by ``(namespace, exporter)``, so two tests
    sharing a namespace would have the *second* one silently reuse the
    *first* test's already-built provider — and therefore the first test's
    exporter instance, not its own — regardless of this monkeypatch.

    ``configure_telemetry()`` is forced first, with the *real*
    ``_build_span_exporter`` still in place, so the global application
    tracer provider is guaranteed already configured before the monkeypatch
    below is applied. ``configure_telemetry`` is idempotent
    (``_state.configured`` gates it) and also wires the *global* provider's
    exporter through ``_build_span_exporter`` on its one-time setup — running
    this test first in a process (no prior test having configured telemetry
    yet) would otherwise silently route the inner
    ``orchestrator.stream_dispatch`` span into this test's exporter too,
    alongside the harness span, purely as an artefact of test execution
    order rather than anything this milestone changed.
    """
    telemetry_module.configure_telemetry()
    monkeypatch.setattr(telemetry_module.exporters, "_build_span_exporter", lambda _exp: exporter)

    llm = FakeLLM(reply=STUB_REPLY, chunks=["hel", "lo", "!"])
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    harness_cfg.metrics_namespace = namespace
    harness_cfg.metrics_exporter = HARNESS_STREAM_SPAN_EXPORTER
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="hi")])
    return wrapper, request


def rag_enabled_settings() -> Settings:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.embeddings.enabled = True
    settings.embeddings.provider = "lmstudio"
    settings.vector.enabled = True
    settings.vector.provider = "chroma"
    return settings
