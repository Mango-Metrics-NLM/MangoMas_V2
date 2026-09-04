"""Fixtures for the tier-1 end-to-end suite (spec-0029 R3).

What makes this tier distinct from the rest of `tests/`: a flow here drives a
request through the **real composition root** — `MANGOMAS_*` env var →
`Settings` → `build_orchestrator` → adapters → orchestrator → the FastAPI app
— and reads the outcome back through a public boundary (an HTTP response, a
persisted turn in `GET /history`, an error envelope). Everything else in the
suite hand-builds an `AgentContext`, which is the right shape for a unit test
and cannot prove an env var reaches a running request.

The LLM is a fake, deliberately: this tier is about the wiring, and a real
model would make it neither fast nor deterministic. The fake is injected
through `llm_registry.scoped` rather than by constructing an `Orchestrator`
directly, so the provider-selection path (`cfg.llm.provider` → registry →
factory → client) is exercised rather than bypassed. That is the same seam
`tests/lmstudio/test_stream_fallback.py` and `tests/test_composition.py`
already use.

**Why the lifespan is skipped.** `create_app(orchestrator=...)` bypasses
`_lifespan`, which calls `configure_telemetry` → `logging.basicConfig(force=True)`
— that rips out pytest's `caplog` handlers for every test that follows in the
session. The lifespan's own wiring is already pinned by
`tests/test_api.py::test_lifespan_startup_and_shutdown`; this tier reuses the
orchestrator it would have built, without the global logging side effect.

Gated by `RUN_INTEGRATION=1` through the directory rule in `tests/conftest.py`.
CI sets it on every push via `make gated-suites`, so these flows are not
optional — they are the tier that must stay fast, hermetic, and green.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from mangomas.adapters.storage import SQLiteRepository
from mangomas.api.app import create_app
from mangomas.composition import build_orchestrator, llm_registry
from mangomas.config import LLMSettings, Settings, get_settings
from mangomas.core import Orchestrator
from tests.constants import ASGI_TEST_BASE_URL, DB_URL_ENV, IN_MEMORY_SQLITE_URL
from tests.fakes import FakeLLM

logger = logging.getLogger(__name__)


@dataclass
class BuiltClients:
    """Every LLM client the composition root built, in construction order.

    The `MODEL_OVERRIDE` flow needs to prove *which* client received a call,
    which means observing construction — `ctx.llm` alone cannot distinguish a
    base client from a per-agent override built by the same factory. Recording
    at the factory is the only vantage point that sees both.
    """

    by_model: dict[str, FakeLLM] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def register(self, model: str, client: FakeLLM) -> FakeLLM:
        self.by_model[model] = client
        self.order.append(model)
        return client

    def called_models(self) -> set[str]:
        """Models whose client actually received at least one request."""
        return {model for model, client in self.by_model.items() if client.calls}


def recording_llm_factory(
    clients: BuiltClients,
    template: FakeLLM | None = None,
    per_model: dict[str, FakeLLM] | None = None,
) -> Callable[[LLMSettings], FakeLLM]:
    """Build an `llm_registry` factory that records every client it constructs.

    Signature-compatible with `_lmstudio_factory` (`LLMSettings -> client`), so
    it can be swapped in via `llm_registry.scoped` and is called for the shared
    client *and* for each per-agent override — `build_agent_llm_overrides`
    resolves the same registry entry, swapping only `cfg.model`.

    *per_model* supplies a distinct fake for a named model; *template* seeds
    every other one. Clients are cached per model so a second construction for
    the same model returns the same instance, matching the real factory's
    dedup behaviour as seen from the caller.
    """
    prototypes = dict(per_model or {})

    def _factory(cfg: LLMSettings) -> FakeLLM:
        existing = clients.by_model.get(cfg.model)
        if existing is not None:
            return existing
        prototype = prototypes.get(cfg.model)
        if prototype is None:
            prototype = _clone(template) if template is not None else FakeLLM()
        logger.debug("Recording-factory built a fake client", extra={"model": cfg.model})
        return clients.register(cfg.model, prototype)

    return _factory


def _clone(template: FakeLLM) -> FakeLLM:
    """Fresh fake carrying the template's scripted behaviour but its own call log.

    A shared instance would let one flow's calls leak into another's
    assertions; copying the *configuration* and not the recorded calls is what
    keeps each constructed client independently assertable.
    """
    return FakeLLM(
        reply=template.reply,
        replies=list(template.replies),
        chunks=list(template.chunks),
        delay_seconds=template.delay_seconds,
        raise_on_stream=template.raise_on_stream,
        raise_after_chunks=template.raise_after_chunks,
    )


@dataclass
class ComposedApp:
    """An app built through the real composition root, plus what a flow asserts on."""

    app: FastAPI
    orchestrator: Orchestrator
    clients: BuiltClients

    @property
    def llm(self) -> FakeLLM:
        """The single built client, for flows that expect exactly one."""
        assert len(self.clients.by_model) == 1, (
            f"expected one built LLM client, got {sorted(self.clients.by_model)}"
        )
        return next(iter(self.clients.by_model.values()))

    def client(self) -> httpx.AsyncClient:
        """An httpx client bound to this app over in-process ASGI transport."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=ASGI_TEST_BASE_URL,
        )


ComposeFn = Callable[..., ComposedApp]


@pytest.fixture
def compose_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[ComposeFn]:
    """Factory fixture: build an app through the composition root.

    A factory rather than a plain fixture because every flow sets a different
    env before building, and `build_orchestrator` reads settings once at build
    time — so the env has to be in place before the call, not after.

    Storage is forced to in-memory SQLite unless a flow overrides it, so a
    developer's real database is never touched by a CI-resident suite.
    """
    orchestrators: list[Orchestrator] = []

    def _compose(
        env: dict[str, str] | None = None,
        *,
        llm: FakeLLM | None = None,
        per_model: dict[str, FakeLLM] | None = None,
    ) -> ComposedApp:
        monkeypatch.setenv(DB_URL_ENV, IN_MEMORY_SQLITE_URL)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        # Settings are cached process-wide; the autouse fixture in
        # tests/conftest.py clears the cache around every test, but the build
        # below must see *this* flow's env, so clear again after setting it.
        get_settings.cache_clear()

        clients = BuiltClients()
        factory = recording_llm_factory(clients, template=llm, per_model=per_model)
        settings = Settings()
        logger.info(
            "Composing tier-1 app",
            extra={
                "llm_provider": settings.llm.provider,
                "db_url": settings.db.url,
                "loop_max_steps": settings.loop.max_steps,
                "loop_step_timeout_seconds": settings.loop.step_timeout_seconds,
            },
        )
        with llm_registry.scoped(settings.llm.provider, factory):
            orchestrator = build_orchestrator(settings)
        orchestrators.append(orchestrator)
        return ComposedApp(
            app=create_app(orchestrator=orchestrator),
            orchestrator=orchestrator,
            clients=clients,
        )

    yield _compose

    for orchestrator in orchestrators:
        repo = orchestrator.context.repo
        if isinstance(repo, SQLiteRepository):
            repo.close()


@pytest.fixture
async def closing_orchestrators() -> AsyncIterator[list[Orchestrator]]:
    """Collect orchestrators a flow wants closed via the real `aclose` path.

    Separate from `compose_app`'s synchronous teardown because `aclose` is
    itself under test in the MODEL_OVERRIDE flow — a flow that asserts on
    close ordering must call it, not have a fixture call it first.
    """
    collected: list[Orchestrator] = []
    yield collected
    for orchestrator in collected:
        await orchestrator.aclose()


async def read_history(
    composed: ComposedApp, *, headers: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """`GET /history` through the app, returning the turns list.

    Shared because five flows read it, and each one asserting on the raw JSON
    shape would mean five places to fix when the envelope changes.
    """
    async with composed.client() as client:
        response = await client.get("/history", headers=headers or {})
    assert response.status_code == 200, response.text
    turns: list[dict[str, Any]] = response.json()["turns"]
    return turns


def turn_content(turn: dict[str, Any]) -> str:
    """The assistant text inside a persisted turn row.

    A row's ``response`` column holds a serialised ``AgentResponse``
    (``content`` / ``agent`` / ``metadata``), not a bare string — a detail
    worth naming once here rather than re-deriving in each flow.
    """
    response: dict[str, Any] = turn["response"]
    content: str = response["content"]
    return content


def turn_metadata(turn: dict[str, Any]) -> dict[str, Any]:
    """The ``metadata`` block of a persisted turn's response."""
    response: dict[str, Any] = turn["response"]
    metadata: dict[str, Any] = response.get("metadata", {})
    return metadata


def turn_prompt(turn: dict[str, Any]) -> str:
    """The last user message of a persisted turn's *request*.

    The request side is what a flow controls directly, so it identifies a row
    even when the fake's reply is the same for every call — which is the case
    on the streaming path, where ``FakeLLM`` yields ``chunks``/``reply`` and
    does not walk ``replies``.
    """
    request: dict[str, Any] = turn["request"]
    messages: list[dict[str, Any]] = request["messages"]
    content: str = messages[-1]["content"]
    return content
