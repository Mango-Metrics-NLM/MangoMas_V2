"""Harness orchestrator wrapper: dispatch, stream, and span lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from mangomas.composition import _HarnessOrchestrator, agent_registry
from mangomas.config import Settings
from mangomas.core.agent import AgentContext, AgentRequest, Message
from mangomas.errors import AgentNotFound
from tests.composition.helpers import (
    HARNESS_STREAM_SPAN_NAMESPACE,
    make_traced_stream_wrapper,
)
from tests.constants import DEFAULT_AGENT_NAME, STUB_REPLY
from tests.fakes import FakeLLM, FakeRepository


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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.stays_open"
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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.no_leak"
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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.early_abandon"
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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.cancelled"
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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.exception"
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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.no_aclose"
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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.aclose_raises"
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
    wrapper, request = make_traced_stream_wrapper(
        monkeypatch, exporter, namespace=f"{HARNESS_STREAM_SPAN_NAMESPACE}.not_found"
    )

    with pytest.raises(AgentNotFound):
        await wrapper.stream_dispatch("no-such-agent", request)

    assert exporter.get_finished_spans() == ()
