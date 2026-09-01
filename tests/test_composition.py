"""Tests for the composition root."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import mangomas.composition as composition_module
import mangomas.telemetry as telemetry_module
from mangomas.adapters.llm import VertexClient
from mangomas.composition import (
    _build_gcp_secrets_provider,
    _file_memory_factory,
    _HarnessOrchestrator,
    _lmstudio_embedding_factory,
    _registries,
    _resolve_llm_secrets,
    _storage_registry,
    _vector_registry,
    _vertex_factory,
    agent_registry,
    build_agent_llm_overrides,
    build_orchestrator,
    embedding_registry,
    llm_registry,
)
from mangomas.config import (
    DEFAULT_GCP_SECRET_VERSION,
    DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS,
    AgentSettings,
    DBSettings,
    EmbeddingSettings,
    LLMSettings,
    MemorySettings,
    SecretsSettings,
    Settings,
    VectorSettings,
)
from mangomas.core import Orchestrator
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.errors import AgentNotFound, ConfigError
from mangomas.secrets import secrets_registry
from tests.constants import DEFAULT_AGENT_NAME, STUB_REPLY
from tests.fakes import (
    FakeEmbeddingClient,
    FakeLLM,
    FakeRepository,
    FakeVectorStore,
    FakeVertexGenerativeModel,
)

# Distinct namespace per streaming-span test so build_scoped_tracer's module-
# level _scoped_tracers cache (keyed by (namespace, exporter)) never returns
# another test's already-built tracer/exporter pair.
_HARNESS_STREAM_SPAN_NAMESPACE: str = "test.harness.stream_span"
_HARNESS_STREAM_SPAN_EXPORTER: Literal["console"] = "console"


def _close_repo(orch: Orchestrator) -> None:
    repo = orch.context.repo
    assert repo is not None
    repo.close()


def test_build_orchestrator_wires_chat_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "chat" in orch.list_agents()
        assert orch.context.llm is not None
        assert orch.context.repo is not None
    finally:
        _close_repo(orch)


def test_build_orchestrator_wires_summarize_agent() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert "summarize" in orch.list_agents()
    finally:
        _close_repo(orch)


def test_build_orchestrator_invokes_agent_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_orchestrator layers in entry-point agents via ensure_agent_plugins."""
    seen: list[tuple[Any, Any]] = []

    def _spy(settings: Any, registry: Any) -> None:
        seen.append((settings, registry))

    monkeypatch.setattr(composition_module, "ensure_agent_plugins", _spy)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert len(seen) == 1
        called_settings, called_registry = seen[0]
        assert called_settings is settings
        assert called_registry is agent_registry
    finally:
        _close_repo(orch)


def test_default_agents_are_registered_in_agent_registry() -> None:
    assert "chat" in agent_registry.available()
    assert "summarize" in agent_registry.available()
    assert "tool" in agent_registry.available()
    assert "planner" in agent_registry.available()
    assert "reviewer" in agent_registry.available()


def test_registry_singletons_are_the_same_object_builder_reads() -> None:
    """Pin the invariant `Registry.scoped()` monkeypatching depends on.

    The facade re-exports registries from ``_registries.py``; ``builder.py``
    imports the very same names. If a future refactor ever gave ``builder.py``
    its own ``Registry("vector")`` instead of importing the shared singleton,
    every ``*_registry.scoped(...)`` test would still run — and would fail
    with a confusing "used the real SDK" or "fake not found" error instead of
    a clear identity mismatch. This test makes the failure legible instead.
    """
    # `builder.py` deliberately does not re-export these (it is not a facade),
    # so mypy's `--no-implicit-reexport` refuses static attribute access;
    # `importlib` + `getattr` reach the same real module attribute at runtime
    # without mypy statically checking it against builder's typed exports —
    # the same idiom `tests/test_import_compat.py` uses throughout.
    builder_mod = importlib.import_module("mangomas.composition.builder")
    assert llm_registry is _registries.llm_registry is builder_mod.llm_registry
    assert embedding_registry is _registries.embedding_registry is builder_mod.embedding_registry
    assert agent_registry is _registries.agent_registry is builder_mod.agent_registry
    assert _storage_registry is _registries._storage_registry
    assert _vector_registry is _registries._vector_registry


def test_build_orchestrator_disabled_harness_returns_plain_orchestrator() -> None:
    """With ``harness.enabled=False`` (default), the harness subclass is not engaged.

    Since spec-0028, this branch still isn't a *bare* ``Orchestrator`` — it's
    ``_Orchestrator``, which mixes in ``_AgentLLMOverrideCloseMixin`` so
    per-agent MODEL_OVERRIDE clients get closed regardless of harness state.
    What this test actually pins is "no harness wrapper," not "no wrapper at
    all" — verified via ``isinstance``, not exact ``type()`` identity.
    """
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch, Orchestrator)
        assert not isinstance(orch, _HarnessOrchestrator)
    finally:
        _close_repo(orch)


def test_build_orchestrator_enabled_harness_returns_wrapper() -> None:
    """With ``harness.enabled=True``, the harness subclass is returned and agents match."""
    baseline_settings = Settings(_env_file=None)  # type: ignore[call-arg]
    baseline_settings.db.url = "sqlite:///:memory:"
    baseline = build_orchestrator(baseline_settings)
    baseline_agents = baseline.list_agents()
    _close_repo(baseline)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.harness.enabled = True
    orch = build_orchestrator(settings)
    try:
        assert isinstance(orch, _HarnessOrchestrator)
        assert orch.list_agents() == baseline_agents
    finally:
        _close_repo(orch)


# ── Vertex factory (uses main's VertexClient + project_id/credentials_path) ──


def test_vertex_factory_is_registered_in_llm_registry() -> None:
    assert "vertex" in llm_registry.available()


def test_vertex_factory_forwards_settings_to_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_vertex_factory`` must forward LLMSettings fields to ``VertexClient``.

    We monkeypatch the ``VertexClient`` symbol imported by ``composition.py``
    with a recorder so the test doesn't need the real Vertex SDK.
    """
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(composition_module, "VertexClient", _Recorder)

    cfg = LLMSettings(
        provider="vertex",
        model="gemini-fake",
        project_id="proj-x",
        location="europe-west4",
        credentials_path="/keys/sa.json",
        timeout_seconds=42.0,
        temperature=0.7,
    )
    _vertex_factory(cfg)
    assert captured == {
        "project_id": "proj-x",
        "location": "europe-west4",
        "model": "gemini-fake",
        "credentials_path": "/keys/sa.json",
        "credentials_json": None,
        "timeout_seconds": 42.0,
        "default_temperature": 0.7,
    }


def test_vertex_factory_uses_resolved_secret_as_credentials_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``secret_ref`` is set, the resolved ``api_key`` becomes ``credentials_json``."""
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(composition_module, "VertexClient", _Recorder)

    cfg = LLMSettings(
        provider="vertex",
        model="gemini-fake",
        project_id="proj-x",
        secret_ref="VERTEX_SA",  # noqa: S106 — test fixture, not a real secret
        api_key='{"client_email": "x@y.z"}',  # would be the resolved secret value
    )
    _vertex_factory(cfg)
    assert captured["credentials_json"] == '{"client_email": "x@y.z"}'
    assert captured["credentials_path"] is None


def test_vertex_factory_builds_vertex_client_with_injected_model() -> None:
    """The seeded vertex factory must accept LLMSettings and yield a VertexClient.

    We swap the factory inside a :meth:`Registry.scoped` block to inject a
    fake :class:`FakeVertexGenerativeModel` so the test does not import the
    real SDK or touch the network.
    """
    fake_model = FakeVertexGenerativeModel(reply="vertex-says-hi")

    def _vertex_factory_with_fake(cfg: LLMSettings) -> VertexClient:
        return VertexClient(
            project_id=cfg.project_id,
            location=cfg.location,
            model=cfg.model,
            client=fake_model,
            timeout_seconds=cfg.timeout_seconds,
            default_temperature=cfg.temperature,
        )

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.llm.provider = "vertex"
    settings.llm.project_id = "test-project"
    settings.llm.model = "gemini-fake"
    settings.db.url = "sqlite:///:memory:"

    with llm_registry.scoped("vertex", _vertex_factory_with_fake):
        orch = build_orchestrator(settings)
        try:
            assert isinstance(orch.context.llm, VertexClient)
        finally:
            _close_repo(orch)


# ── Per-agent MODEL_OVERRIDE wiring (spec-0028 / ADR-0028) ──────────────────


def test_build_agent_llm_overrides_returns_empty_dict_when_no_overrides_set() -> None:
    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    assert build_agent_llm_overrides({"chat": AgentSettings()}, base_cfg) == {}


def test_build_agent_llm_overrides_skips_blank_and_whitespace_override() -> None:
    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {
        "chat": AgentSettings(model_override=""),
        "tool": AgentSettings(model_override="   "),
    }
    assert build_agent_llm_overrides(agents_cfg, base_cfg) == {}


def test_build_agent_llm_overrides_skips_override_equal_to_base_model() -> None:
    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {"chat": AgentSettings(model_override="local-model")}
    assert build_agent_llm_overrides(agents_cfg, base_cfg) == {}


def test_build_agent_llm_overrides_builds_client_for_differing_override() -> None:
    captured: list[LLMSettings] = []

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        captured.append(cfg)
        return FakeLLM()

    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {"planner": AgentSettings(model_override="other-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        result = build_agent_llm_overrides(agents_cfg, base_cfg)

    assert set(result) == {"planner"}
    assert isinstance(result["planner"], FakeLLM)
    assert captured[0].model == "other-model"
    assert captured[0].provider == "lmstudio"


def test_build_agent_llm_overrides_dedups_clients_across_agents() -> None:
    call_count = 0

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return FakeLLM()

    base_cfg = LLMSettings(provider="lmstudio", model="local-model")
    agents_cfg = {
        "planner": AgentSettings(model_override="shared-model"),
        "reviewer": AgentSettings(model_override="shared-model"),
    }
    with llm_registry.scoped("lmstudio", _fake_factory):
        result = build_agent_llm_overrides(agents_cfg, base_cfg)

    assert call_count == 1
    assert result["planner"] is result["reviewer"]


def test_agent_settings_model_override_env_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MANGOMAS_AGENTS__<NAME>__MODEL_OVERRIDE` must reach `Settings().agents[<name>]`.

    Mirrors `test_agent_settings_max_tool_steps_env_round_trip` in
    `tests/test_tool_agent.py` — this env var shape had no test at all before
    spec-0028 wired the field it populates.
    """
    monkeypatch.setenv("MANGOMAS_AGENTS__CHAT__MODEL_OVERRIDE", "override-model")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.agents["chat"].model_override == "override-model"


def test_build_orchestrator_populates_agent_llm_overrides_extra() -> None:
    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        return FakeLLM(reply=f"model={cfg.model}")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.agents = {"chat": AgentSettings(model_override="override-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)
        try:
            overrides = orch.context.extras["agent_llm_overrides"]
            assert set(overrides) == {"chat"}
            assert isinstance(overrides["chat"], FakeLLM)
        finally:
            _close_repo(orch)


def test_build_orchestrator_no_overrides_extra_is_empty_dict() -> None:
    """Backward-compat guard: no MODEL_OVERRIDE configured -> extras key is `{}`."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.extras["agent_llm_overrides"] == {}
    finally:
        _close_repo(orch)


async def test_orchestrator_aclose_closes_agent_llm_override_clients() -> None:
    shared_llm = FakeLLM()
    override_llm = FakeLLM()
    built: dict[str, FakeLLM] = {"local-model": shared_llm, "override-model": override_llm}

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        return built[cfg.model]

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.agents = {"chat": AgentSettings(model_override="override-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)

    await orch.aclose()

    assert shared_llm.closed is True
    assert override_llm.closed is True


async def test_orchestrator_aclose_without_overrides_matches_prior_behavior() -> None:
    """No MODEL_OVERRIDE configured -> aclose() closes only the shared LLM, as before."""
    shared_llm = FakeLLM()

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:  # noqa: ARG001
        return shared_llm

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)

    await orch.aclose()  # must not raise

    assert shared_llm.closed is True


async def test_harness_orchestrator_aclose_closes_agent_llm_override_clients() -> None:
    """The harness-enabled branch (_HarnessOrchestrator) also picks up the mixin."""
    shared_llm = FakeLLM()
    override_llm = FakeLLM()
    built: dict[str, FakeLLM] = {"local-model": shared_llm, "override-model": override_llm}

    def _fake_factory(cfg: LLMSettings) -> FakeLLM:
        return built[cfg.model]

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.harness.enabled = True
    settings.agents = {"chat": AgentSettings(model_override="override-model")}
    with llm_registry.scoped("lmstudio", _fake_factory):
        orch = build_orchestrator(settings)

    assert isinstance(orch, _HarnessOrchestrator)
    await orch.aclose()

    assert shared_llm.closed is True
    assert override_llm.closed is True


def test_build_orchestrator_wires_all_agents() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = False
    orch = build_orchestrator(settings)
    try:
        agents = orch.list_agents()
        assert "chat" in agents
        assert "summarize" in agents
        assert "tool" in agents
        assert "planner" in agents
        assert "reviewer" in agents
        assert orch.context.memory is None
    finally:
        _close_repo(orch)


def test_build_orchestrator_with_memory_enabled_attaches_repo(tmp_path: Path) -> None:
    """The ``memory.enabled=True`` branch wires a :class:`FileMemoryRepository`."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.memory.enabled = True
    settings.memory.memory_dir = str(tmp_path / "mem")
    orch = build_orchestrator(settings)
    try:
        assert orch.context.memory is not None
    finally:
        _close_repo(orch)


def test_file_memory_factory_returns_repository(tmp_path: Path) -> None:
    """Direct factory smoke test — keeps the factory hook covered for refactors."""
    cfg = MemorySettings(
        enabled=True,
        memory_dir=str(tmp_path / "mem"),
    )
    repo = _file_memory_factory(cfg)
    assert repo is not None
    repo.close()


async def test_harness_orchestrator_dispatch_wraps_through_to_baseline() -> None:
    """The harness wrapper's dispatch returns the same content as the inner orchestrator."""
    llm = FakeLLM(reply=STUB_REPLY)
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    # Register an agent from the registry so we exercise the real dispatch path.
    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="ping")])
    response = await wrapper.dispatch(DEFAULT_AGENT_NAME, request)

    assert response.content == STUB_REPLY
    # The wrapper persisted the turn via the inner orchestrator.
    assert len(llm.calls) == 1


async def test_harness_orchestrator_stream_dispatch_yields_tokens() -> None:
    """The streaming wrapper still emits tokens through the inner orchestrator."""
    llm = FakeLLM(reply=STUB_REPLY, chunks=["hel", "lo"])
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="hi")])
    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)
    received = [chunk async for chunk in stream]

    assert "".join(received)  # at least one non-empty token


async def test_harness_traced_stream_full_drain_persists_exactly_one_turn() -> None:
    """Spec-0025 composes under the harness wrapper: a full drain through
    ``_traced_stream`` persists the streamed turn exactly once. The wrapper's
    ``finally``-side ``inner.aclose()`` runs on an already-exhausted inner
    generator (persistence happened during the final ``__anext__``), so it
    must neither double-persist nor raise."""
    llm = FakeLLM(reply=STUB_REPLY, chunks=["hel", "lo"])
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)
    wrapper.register(agent_registry.get(DEFAULT_AGENT_NAME)(None))

    request = AgentRequest(messages=[Message(role="user", content="hi")])
    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)
    received = [chunk async for chunk in stream]

    assert "".join(received) == "hello"
    rows = await repo.list_turns()
    assert len(rows) == 1
    assert rows[0]["response"]["content"] == "hello"
    assert rows[0]["response"]["metadata"]["stream"] == {"chunks": 2, "degraded": False}


async def test_harness_traced_stream_abandonment_persists_nothing() -> None:
    """Early abandonment through the wrapper (``aclose`` → ``GeneratorExit``
    propagated into the inner generator by ``_traced_stream``'s cleanup) never
    saves a half-drained turn — the spec-0025 rule survives the wrap."""
    llm = FakeLLM(reply=STUB_REPLY, chunks=["hel", "lo"])
    repo = FakeRepository()
    ctx = AgentContext(llm=llm, repo=repo)

    harness_cfg = Settings(_env_file=None).harness  # type: ignore[call-arg]
    harness_cfg.enabled = True
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)
    wrapper.register(agent_registry.get(DEFAULT_AGENT_NAME)(None))

    request = AgentRequest(messages=[Message(role="user", content="hi")])
    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)
    assert await stream.__anext__() == "hel"
    await cast("AsyncGenerator[str, None]", stream).aclose()

    assert await repo.list_turns() == []


def _make_traced_stream_wrapper(
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
    harness_cfg.metrics_exporter = _HARNESS_STREAM_SPAN_EXPORTER
    wrapper = _HarnessOrchestrator(ctx, harness_cfg)

    factory = agent_registry.get(DEFAULT_AGENT_NAME)
    wrapper.register(factory(None))

    request = AgentRequest(messages=[Message(role="user", content="hi")])
    return wrapper, request


async def test_harness_stream_span_stays_open_until_the_stream_is_fully_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the harness span used to open and close around
    ``super().stream_dispatch(...)`` alone — a coroutine call that returns an
    *unconsumed* async generator without running any of its body — so the
    span's recorded duration measured "time to validate the agent name", not
    the stream. This proves the span is still *open* (unexported) while
    tokens are still being consumed, and only finishes once the consumer has
    drained every chunk.
    """
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.stays_open"
    )

    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)

    chunks_seen = 0
    async for _chunk in stream:
        chunks_seen += 1
        # Mid-drain: the harness span must not have been exported yet.
        assert exporter.get_finished_spans() == ()
    assert chunks_seen > 0

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "harness.agent_invoke"
    assert finished[0].attributes is not None
    assert finished[0].attributes["harness.topology"] == "stream"


async def test_harness_stream_span_does_not_leak_into_the_consumers_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the *fix's own* failure mode: holding
    ``start_as_current_span`` across a ``yield`` would make the harness span
    "current" in the consumer's ambient context between chunks, so every
    span the consumer creates while iterating would become a child of
    ``harness.agent_invoke`` instead of whatever it should actually parent to.
    The consumer here creates no span of its own, so its current span must
    stay the OTel no-op sentinel throughout iteration — never the harness span.
    """
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.no_leak"
    )

    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)

    async for _chunk in stream:
        current = trace.get_current_span()
        assert not current.get_span_context().is_valid, (
            "harness span leaked into the consumer's context between chunks"
        )


async def test_harness_stream_span_ends_on_early_consumer_abandonment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumer that stops iterating early (e.g. an HTTP client disconnect,
    which ``StreamingResponse`` surfaces as the generator's own ``aclose()``)
    must still end the harness span — it must not stay open until garbage
    collection, and it must not raise.
    """
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.early_abandon"
    )

    stream = await wrapper.stream_dispatch(DEFAULT_AGENT_NAME, request)
    first = await stream.__anext__()
    assert first
    assert exporter.get_finished_spans() == ()

    # ``stream_dispatch`` is typed as the more general AsyncIterator[str] (the
    # StreamingAgent.stream protocol doesn't guarantee aclose()), but
    # _traced_stream's concrete return value always is one — this is exactly
    # what's under test.
    await cast("AsyncGenerator[str, None]", stream).aclose()

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "harness.agent_invoke"


async def test_harness_stream_span_ends_on_task_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sibling test above drives GeneratorExit via an explicit
    ``.aclose()`` — the ``except (GeneratorExit, asyncio.CancelledError)``
    branch's other half was previously untested. Cancelling the *task*
    consuming the stream while ``_traced_stream`` is itself suspended
    inside ``await inner.__anext__()`` throws ``CancelledError`` into that
    exact point, distinct from an external ``aclose()`` call."""
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.cancelled"
    )

    resumed_after_first_chunk = asyncio.Event()

    async def _slow_inner() -> AsyncGenerator[str, None]:
        yield "first"
        resumed_after_first_chunk.set()
        await asyncio.sleep(10)
        yield "second"  # pragma: no cover -- unreachable, cancelled before this resumes

    async def _consume() -> None:
        async for _chunk in wrapper._traced_stream(DEFAULT_AGENT_NAME, request, _slow_inner()):
            pass

    task = asyncio.create_task(_consume())
    await resumed_after_first_chunk.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].status.status_code != StatusCode.ERROR


async def test_harness_traced_stream_records_exception_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine failure from *inner* (not GeneratorExit/CancelledError) must
    propagate to the consumer, and the span must record it as an error —
    mirroring what ``start_as_current_span`` would have done automatically,
    which this design forgoes in exchange for not leaking context."""
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.exception"
    )

    async def _failing_inner() -> AsyncGenerator[str, None]:
        yield "partial"
        raise RuntimeError("boom")

    stream = wrapper._traced_stream(DEFAULT_AGENT_NAME, request, _failing_inner())

    received: list[str] = []
    with pytest.raises(RuntimeError, match="boom"):
        async for chunk in stream:
            received.append(chunk)
    assert received == ["partial"]

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].status.status_code == StatusCode.ERROR
    assert len(finished[0].events) == 1  # record_exception() adds an event


async def test_harness_traced_stream_handles_inner_without_aclose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``StreamingAgent.stream()`` is typed as a plain ``AsyncIterator[str]``
    — ``aclose()`` isn't guaranteed, even though every built-in
    implementation (an async generator) always has one. A minimal custom
    iterator without one must not crash the harness wrapper's cleanup."""
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.no_aclose"
    )

    class _NoAcloseIterator:
        def __init__(self, items: list[str]) -> None:
            self._items = iter(items)

        def __aiter__(self) -> _NoAcloseIterator:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._items)
            except StopIteration:
                raise StopAsyncIteration from None

    stream = wrapper._traced_stream(DEFAULT_AGENT_NAME, request, _NoAcloseIterator(["a", "b"]))
    received = [chunk async for chunk in stream]
    assert received == ["a", "b"]

    finished = exporter.get_finished_spans()
    assert len(finished) == 1


async def test_harness_traced_stream_ends_span_even_when_inner_aclose_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``inner.aclose()`` is called unconditionally in the ``finally`` block,
    including on a normal full drain. If it raises (a misbehaving custom
    ``AsyncIterator``), that failure must not suppress ``span.end()`` — else
    the harness span leaks (never exported) — and must not mask the stream's
    otherwise-successful result for the consumer."""
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.aclose_raises"
    )

    class _RaisingAcloseIterator:
        def __init__(self, items: list[str]) -> None:
            self._items = iter(items)

        def __aiter__(self) -> _RaisingAcloseIterator:
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._items)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            raise RuntimeError("aclose boom")

    stream = wrapper._traced_stream(DEFAULT_AGENT_NAME, request, _RaisingAcloseIterator(["a", "b"]))
    received = [chunk async for chunk in stream]
    assert received == ["a", "b"]

    finished = exporter.get_finished_spans()
    assert len(finished) == 1


async def test_harness_stream_dispatch_agent_not_found_raises_before_any_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AgentNotFound`` must still raise eagerly (before streaming begins,
    unchanged from the base ``Orchestrator``), and — since a 404 is not an
    agent invocation — must not produce a ``harness.agent_invoke`` span."""
    exporter = InMemorySpanExporter()
    wrapper, request = _make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{_HARNESS_STREAM_SPAN_NAMESPACE}.not_found"
    )

    with pytest.raises(AgentNotFound):
        await wrapper.stream_dispatch("no-such-agent", request)

    assert exporter.get_finished_spans() == ()


# ── Embeddings wiring ─────────────────────────────────────────────────────────


def test_embedding_factories_registered() -> None:
    available = embedding_registry.available()
    assert "lmstudio" in available
    assert "sentence_transformers" in available
    assert "vertex" in available


def test_build_orchestrator_embeddings_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.embeddings is None
    finally:
        _close_repo(orch)


def test_build_orchestrator_embeddings_enabled_attaches_client() -> None:
    """When ``embeddings.enabled=True`` the factory result is attached to the context."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.embeddings.enabled = True
    settings.embeddings.provider = "lmstudio"

    fake = FakeEmbeddingClient()
    with embedding_registry.scoped("lmstudio", lambda _cfg: fake):
        orch = build_orchestrator(settings)
        try:
            assert orch.context.embeddings is fake
        finally:
            _close_repo(orch)


def test_lmstudio_embedding_factory_forwards_settings() -> None:
    cfg = EmbeddingSettings(
        enabled=True,
        provider="lmstudio",
        model="embed-x",
        base_url="http://lm/v1",
        timeout_seconds=42.0,
    )
    client = _lmstudio_embedding_factory(cfg)
    assert client._model == "embed-x"
    assert client._base_url == "http://lm/v1"


def test_sentence_transformers_embedding_factory_forwards_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK-backed factories are lazy-imported inside the function body, so
    only registry membership was previously exercised — kwarg-forwarding
    regressions in these bodies were invisible to the suite. Monkeypatching
    the class at its lazy-import source (rather than through the composition
    facade) mirrors ``test_vertex_factory_forwards_settings_to_client`` above,
    adapted for a factory that imports directly instead of via a facade name.
    """
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "mangomas.adapters.embeddings.sentence_transformers.SentenceTransformersEmbeddingClient",
        _Recorder,
    )
    cfg = EmbeddingSettings(enabled=True, provider="sentence_transformers", model="all-MiniLM-L6")
    from mangomas.composition.embeddings import (  # noqa: PLC0415
        _sentence_transformers_embedding_factory,
    )

    _sentence_transformers_embedding_factory(cfg)
    assert captured == {"model": "all-MiniLM-L6"}


def test_vertex_embedding_factory_forwards_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "mangomas.adapters.embeddings.vertex.VertexEmbeddingClient",
        _Recorder,
    )
    cfg = EmbeddingSettings(
        enabled=True,
        provider="vertex",
        model="text-embedding-004",
        project_id="proj-x",
        location="europe-west4",
    )
    from mangomas.composition.embeddings import _vertex_embedding_factory  # noqa: PLC0415

    _vertex_embedding_factory(cfg)
    assert captured == {
        "project_id": "proj-x",
        "location": "europe-west4",
        "model": "text-embedding-004",
    }


# ── Vector store wiring ───────────────────────────────────────────────────────


def test_vector_factory_registered() -> None:
    assert "chroma" in _vector_registry.available()


def test_chroma_vector_factory_forwards_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """See ``test_sentence_transformers_embedding_factory_forwards_settings`` —
    same lazy-import blind spot, same fix."""
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "mangomas.adapters.vector.chroma.ChromaVectorStore",
        _Recorder,
    )
    from mangomas.composition.vector import _chroma_vector_factory  # noqa: PLC0415

    cfg = VectorSettings(enabled=True, provider="chroma", persist_dir="./data/x", collection="c1")
    _chroma_vector_factory(cfg)
    assert captured == {"persist_dir": "./data/x", "collection_name": "c1"}


def test_build_orchestrator_vector_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.vector_store is None
    finally:
        _close_repo(orch)


def test_build_orchestrator_vector_enabled_attaches_store() -> None:
    """When ``vector.enabled=True`` the factory result is attached to the context."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.vector.enabled = True
    settings.vector.provider = "chroma"

    fake = FakeVectorStore()
    with _vector_registry.scoped("chroma", lambda _cfg: fake):
        orch = build_orchestrator(settings)
        try:
            assert orch.context.vector_store is fake
        finally:
            _close_repo(orch)


# ── RAG retrieval tool wiring ─────────────────────────────────────────────────


def _rag_enabled_settings() -> Settings:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.embeddings.enabled = True
    settings.embeddings.provider = "lmstudio"
    settings.vector.enabled = True
    settings.vector.provider = "chroma"
    return settings


def test_build_orchestrator_wires_retrieval_tool_when_both_enabled() -> None:
    settings = _rag_enabled_settings()
    with (
        embedding_registry.scoped("lmstudio", lambda _cfg: FakeEmbeddingClient()),
        _vector_registry.scoped("chroma", lambda _cfg: FakeVectorStore()),
    ):
        orch = build_orchestrator(settings)
        try:
            tools = orch.context.tools
            assert tools is not None
            assert "retrieve" in tools.available()
        finally:
            _close_repo(orch)


def test_build_orchestrator_no_tools_when_only_embeddings_enabled() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    settings.embeddings.enabled = True
    settings.embeddings.provider = "lmstudio"
    with embedding_registry.scoped("lmstudio", lambda _cfg: FakeEmbeddingClient()):
        orch = build_orchestrator(settings)
        try:
            assert orch.context.tools is None
        finally:
            _close_repo(orch)


def test_build_orchestrator_no_tools_when_rag_disabled() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = "sqlite:///:memory:"
    orch = build_orchestrator(settings)
    try:
        assert orch.context.tools is None
    finally:
        _close_repo(orch)


# ── Postgres + GCP Secret Manager registration (v0.3.0 cloud swap-in) ────────


def test_postgres_factory_registered_in_storage_registry() -> None:
    assert "postgres" in _storage_registry.available()


def test_postgres_factory_constructs_repo_without_io() -> None:
    """The postgres factory must not open a pool — preserves the sync shape."""
    factory = _storage_registry.get("postgres")
    cfg = DBSettings(provider="postgres", url="postgresql://h/db")
    repo = factory(cfg)
    # Lazy-pool invariant from PostgresRepository.
    assert repo._pool is None


def test_gcp_secrets_lazy_registers_on_build_when_provider_selected() -> None:
    """When secrets.provider='gcp', build_orchestrator must lazy-register it."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
        secrets=SecretsSettings(provider="gcp", project_id="test-proj"),
    )
    # Drop any prior gcp binding so we can observe the lazy registration,
    # restoring it in `finally` — build_orchestrator registers a *real*
    # GCPSecretManagerProvider into this process-wide singleton, and leaving
    # it there would leak into every test that runs after this one.
    prior = secrets_registry._store.pop("gcp", None)

    captured: dict[str, Any] = {}

    def _capturing_llm_factory(cfg: LLMSettings) -> object:
        captured["api_key"] = cfg.api_key

        class _Stub:
            async def aclose(self) -> None: ...

        return _Stub()

    try:
        with llm_registry.scoped("lmstudio", _capturing_llm_factory):
            orch = build_orchestrator(settings)
            try:
                assert "gcp" in secrets_registry.available()
            finally:
                _close_repo(orch)
    finally:
        if prior is None:
            secrets_registry._store.pop("gcp", None)
        else:
            secrets_registry._store["gcp"] = prior


def test_gcp_secrets_lazy_register_raises_when_project_id_missing() -> None:
    """build_orchestrator must surface the ConfigError eagerly when misconfigured."""
    settings = Settings(
        llm=LLMSettings(provider="lmstudio", api_key="inline"),
        db=DBSettings(provider="sqlite", url="sqlite:///:memory:"),
        secrets=SecretsSettings(provider="gcp", project_id=None),
    )
    prior = secrets_registry._store.pop("gcp", None)
    try:
        with pytest.raises(ConfigError, match="MANGOMAS_SECRETS__PROJECT_ID"):
            build_orchestrator(settings)
    finally:
        if prior is None:
            secrets_registry._store.pop("gcp", None)
        else:
            secrets_registry._store["gcp"] = prior


def test_build_gcp_secrets_provider_returns_provider_when_valid() -> None:
    """Success branch of _build_gcp_secrets_provider — config valid, no SDK call."""
    cfg = SecretsSettings(
        provider="gcp",
        project_id="my-proj",
        timeout_seconds=DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS,
        default_version=DEFAULT_GCP_SECRET_VERSION,
    )
    provider = _build_gcp_secrets_provider(cfg)
    assert provider is not None
    assert provider._project_id == "my-proj"


def test_build_gcp_secrets_provider_raises_without_project_id() -> None:
    """_build_gcp_secrets_provider must raise ConfigError when project_id is None."""
    cfg = SecretsSettings(provider="gcp", project_id=None)
    with pytest.raises(ConfigError, match="MANGOMAS_SECRETS__PROJECT_ID"):
        _build_gcp_secrets_provider(cfg)


def test_resolve_llm_secrets_returns_unchanged_when_no_ref() -> None:
    """_resolve_llm_secrets returns input unchanged when secret_ref is unset."""
    cfg = LLMSettings(provider="lmstudio", api_key="inline-key")
    result = _resolve_llm_secrets(cfg, "env")
    assert result is cfg  # same object, unmodified
    assert result.api_key == "inline-key"


def test_resolve_llm_secrets_updates_when_provider_resolves() -> None:
    """_resolve_llm_secrets updates api_key when provider.get succeeds."""
    cfg = LLMSettings(
        provider="lmstudio",
        api_key="inline",
        secret_ref="my-secret",  # noqa: S106
    )

    class _FakeProvider:
        def get(self, ref: str) -> str | None:
            return "resolved-key" if ref == "my-secret" else None

    with secrets_registry.scoped("fake", _FakeProvider()):
        result = _resolve_llm_secrets(cfg, "fake")
    assert result.api_key == "resolved-key"
    assert result.secret_ref == "my-secret"  # unchanged  # noqa: S105


def test_resolve_llm_secrets_keeps_inline_key_when_provider_returns_none() -> None:
    """When the provider has no value for ``secret_ref``, the inline ``api_key``
    survives unchanged — local development with no vault entry configured
    must keep working rather than resolving to ``None``."""
    cfg = LLMSettings(
        provider="lmstudio",
        api_key="inline-fallback",
        secret_ref="unconfigured-ref",  # noqa: S106
    )

    class _EmptyProvider:
        def get(self, _ref: str) -> str | None:
            return None

    with secrets_registry.scoped("empty", _EmptyProvider()):
        result = _resolve_llm_secrets(cfg, "empty")
    assert result.api_key == "inline-fallback"
    assert result is cfg  # unresolved falls back to the original object, no copy
