"""Single orchestrator: routes a request to a named agent and persists the turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

from opentelemetry import trace

from mangomas.core.agent import (
    Agent,
    AgentContext,
    AgentRequest,
    AgentResponse,
    Message,
    StreamingAgent,
)
from mangomas.core.loop import AcceptanceFn
from mangomas.errors import AgentNotFound, MaxStepsExceeded

logger = logging.getLogger(__name__)


class Orchestrator:
    """Registers agents by name and dispatches requests."""

    def __init__(self, ctx: AgentContext) -> None:
        self._ctx = ctx
        self._agents: dict[str, Agent] = {}
        self._closed: bool = False

    @property
    def context(self) -> AgentContext:
        """The shared runtime context (LLM client, repository, extras)."""
        return self._ctx

    async def aclose(self) -> None:
        """Release adapter resources cleanly. Idempotent and fault-tolerant.

        Dispatches on ``hasattr(component, "aclose")`` to support async-pool
        backends (PostgresRepository, LMStudioClient) while still respecting
        the sync ``close()`` contract used by SQLite. The same teardown rules
        previously lived in the CLI, the FastAPI lifespan, and the demo
        scripts — centralising them here means every entry point shares a
        single tested code path and cannot diverge.

        Fault tolerance: each component's close hook runs under its own
        ``try/except``, so a failure in one (e.g. LLM aclose timing out)
        does not prevent the others (repo, memory) from being closed.
        The first encountered exception is re-raised after every other
        hook has been attempted, preserving the original failure for the
        caller while still releasing the remaining resources.

        Idempotency: ``_closed`` is latched in a ``finally`` so a second
        call is a safe no-op, but only after a complete teardown attempt.
        Callers may invoke this defensively from nested ``finally`` blocks
        without risking double-close errors.
        """
        if self._closed:
            logger.debug("Orchestrator.aclose: already closed, skipping")
            return

        first_exc: BaseException | None = None
        try:
            for label, closer in self._close_hooks():
                try:
                    await closer()
                except Exception as exc:
                    logger.exception("Orchestrator.aclose: %s close failed", label)
                    first_exc = first_exc or exc
        finally:
            self._closed = True
            logger.debug("Orchestrator.aclose: complete")

        if first_exc is not None:
            raise first_exc

    def _close_hooks(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        """Build the ordered list of (label, async close hook) for present components.

        Each hook dispatches on the component's own teardown contract — async
        ``aclose`` where available, sync ``close`` otherwise — so :meth:`aclose`
        can run them uniformly under per-hook fault isolation.
        """
        ctx = self._ctx
        hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        if hasattr(ctx.llm, "aclose"):
            hooks.append(("LLM", ctx.llm.aclose))
        if (repo := ctx.repo) is not None:

            async def _close_repo() -> None:
                if hasattr(repo, "aclose"):
                    await repo.aclose()
                else:
                    repo.close()

            hooks.append(("repo", _close_repo))
        if (memory := ctx.memory) is not None:

            async def _close_memory() -> None:
                memory.close()

            hooks.append(("memory", _close_memory))
        if ctx.embeddings is not None and hasattr(ctx.embeddings, "aclose"):
            hooks.append(("embeddings", ctx.embeddings.aclose))
        if ctx.vector_store is not None and hasattr(ctx.vector_store, "aclose"):
            hooks.append(("vector_store", ctx.vector_store.aclose))
        return hooks

    def register(self, agent: Agent) -> None:
        """Register an agent. Last registration wins for a given name."""
        if not agent.name:
            raise ValueError("Agent must have a non-empty name")
        self._agents[agent.name] = agent
        logger.debug("Registered agent %r", agent.name)

    def list_agents(self) -> list[str]:
        """Return the names of registered agents."""
        return sorted(self._agents)

    async def dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
        *,
        acceptance_fn: AcceptanceFn | None = None,
        max_steps: int | None = None,
    ) -> AgentResponse:
        """Route *request* to the named agent, iterating until accepted or exhausted.

        Parameters
        ----------
        agent_name:
            Name of the registered agent to invoke.
        request:
            The initial request. ``request.max_steps`` sets the default iteration
            limit unless overridden by the *max_steps* keyword argument.
        acceptance_fn:
            Optional callable ``(AgentResponse) -> bool``.  When provided, the
            loop continues until it returns ``True`` or *effective_max* steps are
            exhausted — in which case :class:`~mangomas.errors.MaxStepsExceeded`
            is raised.  When omitted the agent is called ``effective_max`` times
            (default 1) and the last response is returned without raising.
        max_steps:
            Override the per-request ``max_steps`` field.  Must be >= 1.
        """
        if max_steps is not None and max_steps < 1:
            raise ValueError("max_steps must be >= 1")

        if agent_name not in self._agents:
            logger.error(
                "Dispatch failed: agent not found",
                extra={"agent_name": agent_name, "registered": self.list_agents()},
            )
            raise AgentNotFound(agent_name)

        agent = self._agents[agent_name]
        effective_max = max_steps if max_steps is not None else request.max_steps

        with trace.get_tracer(__name__).start_as_current_span("orchestrator.dispatch") as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("messages.count", len(request.messages))
            span.set_attribute("loop.max_steps", effective_max)

            current_messages = list(request.messages)
            response: AgentResponse | None = None
            accepted = False
            steps_taken = 0

            for _ in range(effective_max):
                steps_taken += 1
                step_request = AgentRequest(
                    messages=current_messages,
                    metadata=request.metadata,
                    max_steps=request.max_steps,
                )
                response = await agent.handle(step_request, self._ctx)

                if acceptance_fn is not None:
                    if acceptance_fn(response):
                        accepted = True
                        logger.debug(
                            "Acceptance criteria met at step %d for agent %r",
                            steps_taken,
                            agent_name,
                        )
                        break
                    # Prepare for next iteration: re-inject the assistant reply.
                    current_messages = [
                        *current_messages,
                        Message(role="assistant", content=response.content),
                    ]
                elif effective_max > 1:
                    # Multi-step without acceptance: inject for conversational continuity.
                    current_messages = [
                        *current_messages,
                        Message(role="assistant", content=response.content),
                    ]

            if acceptance_fn is not None and not accepted:
                raise MaxStepsExceeded(effective_max)

            assert response is not None  # noqa: S101 — guaranteed: effective_max >= 1

            # Embed loop telemetry into the response metadata.
            response = AgentResponse(
                content=response.content,
                agent=response.agent,
                metadata={
                    **response.metadata,
                    "loop": {"steps_taken": steps_taken, "accepted": accepted},
                },
            )

            span.set_attribute("loop.steps_taken", steps_taken)
            span.set_attribute("loop.accepted", accepted)

        if self._ctx.repo is not None:
            await self._ctx.repo.save_turn(agent_name, request, response)

        return response

    # ── Multi-agent topologies ────────────────────────────────────────────────

    async def dispatch_pipeline(
        self, agent_names: list[str], request: AgentRequest
    ) -> AgentResponse:
        """Sequential pipeline: each agent's output becomes the next agent's input.

        Raises ``ValueError`` when *agent_names* is empty.
        Raises :class:`~mangomas.errors.AgentNotFound` if any name is unregistered.
        """
        if not agent_names:
            raise ValueError("agent_names must be non-empty")

        with trace.get_tracer(__name__).start_as_current_span(
            "orchestrator.dispatch_pipeline"
        ) as span:
            span.set_attribute("topology", "pipeline")
            span.set_attribute("agent_count", len(agent_names))

            current_request = request
            response: AgentResponse | None = None

            for i, name in enumerate(agent_names):
                logger.debug("Pipeline step %d/%d: agent %r", i + 1, len(agent_names), name)
                response = await self.dispatch(name, current_request)
                if i < len(agent_names) - 1:
                    current_request = AgentRequest(
                        messages=[Message(role="user", content=response.content)],
                        metadata=response.metadata,
                    )

        assert response is not None  # noqa: S101 — guaranteed: at least one step ran
        return response

    async def dispatch_fan_out(
        self, agent_names: list[str], request: AgentRequest
    ) -> list[AgentResponse]:
        """Fan-out: dispatch *request* to all agents in parallel, return results in order.

        Raises ``ValueError`` when *agent_names* is empty.
        Any exception from any agent propagates immediately (fail-fast).
        """
        if not agent_names:
            raise ValueError("agent_names must be non-empty")

        with trace.get_tracer(__name__).start_as_current_span(
            "orchestrator.dispatch_fan_out"
        ) as span:
            span.set_attribute("topology", "fan_out")
            span.set_attribute("agent_count", len(agent_names))

            results = cast(
                list[AgentResponse],
                await asyncio.gather(*(self.dispatch(name, request) for name in agent_names)),
            )

        logger.debug("Fan-out complete: %d responses", len(results))
        return results

    # ── Streaming ─────────────────────────────────────────────────────────────

    def agent_supports_streaming(self, agent_name: str) -> bool:
        """Return whether the named agent implements :class:`StreamingAgent`.

        Raises :class:`~mangomas.errors.AgentNotFound` for an unknown name,
        mirroring :meth:`stream_dispatch`'s eager pre-check (same window: a
        transport layer calls this before it starts streaming, so the 4xx
        envelope can still run).

        This exists so transport layers can label a degraded stream — one
        served by the buffered ``handle()`` fallback — without widening the
        iterator contract: :meth:`stream_dispatch` keeps yielding plain
        ``str`` chunks, and the degradation flag travels out-of-band (e.g. as
        an SSE metadata event) instead of being injected into the token
        stream.
        """
        if agent_name not in self._agents:
            raise AgentNotFound(agent_name)
        return isinstance(self._agents[agent_name], StreamingAgent)

    async def stream_dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
    ) -> AsyncIterator[str]:
        """Validate the agent then return a streaming token iterator.

        Raises :class:`~mangomas.errors.AgentNotFound` immediately (before any
        streaming begins) so that the HTTP layer can return a 4xx response
        instead of starting a streaming response and then failing mid-stream.

        If the agent implements :class:`~mangomas.core.agent.StreamingAgent`
        its ``stream()`` method is used; otherwise ``handle()`` is called and
        the single response is yielded as one chunk (with a warning logged —
        the stream is degraded).

        On a **full drain** the accumulated turn is persisted via
        ``ctx.repo.save_turn`` exactly like :meth:`dispatch` (spec-0025 /
        ADR-0025). A half-drained stream — early consumer abandonment or an
        upstream error — is not a turn and is never persisted.
        """
        if agent_name not in self._agents:
            logger.error(
                "Stream dispatch failed: agent not found",
                extra={"agent_name": agent_name, "registered": self.list_agents()},
            )
            raise AgentNotFound(agent_name)

        agent = self._agents[agent_name]
        logger.debug("Stream dispatch to agent %r", agent_name)
        return self._stream_agent(agent, agent_name, request)

    async def _stream_agent(
        self,
        agent: Agent,
        agent_name: str,
        request: AgentRequest,
    ) -> AsyncIterator[str]:
        """Internal async generator — yields tokens from the agent.

        Persistence deliberately sits *after* the token loop, in the
        normal-completion path — **not** in a ``finally``. In an async
        generator the code after the last ``yield`` only runs when the
        consumer drains to exhaustion; abandonment (``aclose()`` →
        ``GeneratorExit`` thrown in at the suspended ``yield``) and upstream
        exceptions both unwind past it, so a half-drained stream can never be
        saved as a turn (spec-0025's persist-only-on-full-drain rule).
        """
        with trace.get_tracer(__name__).start_as_current_span(
            "orchestrator.stream_dispatch"
        ) as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("messages.count", len(request.messages))

            degraded = not isinstance(agent, StreamingAgent)
            span.set_attribute("stream.degraded", degraded)

            chunks: list[str] = []
            if isinstance(agent, StreamingAgent):
                async for chunk in await agent.stream(request, self._ctx):
                    chunks.append(chunk)
                    yield chunk
            else:
                logger.warning(
                    "Streaming requested but agent %r does not implement "
                    "StreamingAgent; output was buffered into a single chunk",
                    agent_name,
                    extra={"agent": agent_name},
                )
                response = await agent.handle(request, self._ctx)
                chunks.append(response.content)
                yield response.content

            # Full drain: only reached when the consumer exhausts the stream.
            span.set_attribute("stream.chunks", len(chunks))
            if self._ctx.repo is not None:
                streamed_response = AgentResponse(
                    content="".join(chunks),
                    agent=agent_name,
                    metadata={"stream": {"chunks": len(chunks), "degraded": degraded}},
                )
                await self._ctx.repo.save_turn(agent_name, request, streamed_response)
                logger.debug("Persisted streamed turn for agent %r", agent_name)
