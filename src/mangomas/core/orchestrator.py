"""Single orchestrator: routes a request to a named agent and persists the turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
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
        """Release adapter resources cleanly. Idempotent.

        Dispatches on ``hasattr(component, "aclose")`` to support async-pool
        backends (PostgresRepository, LMStudioClient) while still respecting
        the sync ``close()`` contract used by SQLite. The same teardown rules
        previously lived in the CLI and demo scripts — centralising them here
        means every entry point (CLI, FastAPI lifespan, scripts) shares a
        single tested code path and cannot diverge.

        Subsequent calls are no-ops, so callers can invoke this defensively
        from nested ``finally`` blocks without risking double-close errors.
        """
        if self._closed:
            logger.debug("Orchestrator.aclose: already closed, skipping")
            return
        self._closed = True

        if hasattr(self._ctx.llm, "aclose"):
            await self._ctx.llm.aclose()
        if self._ctx.repo is not None:
            if hasattr(self._ctx.repo, "aclose"):
                await self._ctx.repo.aclose()
            else:
                self._ctx.repo.close()
        if self._ctx.memory is not None:
            self._ctx.memory.close()

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
        the single response is yielded as one chunk.
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
        """Internal async generator — yields tokens from the agent."""
        with trace.get_tracer(__name__).start_as_current_span(
            "orchestrator.stream_dispatch"
        ) as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("messages.count", len(request.messages))

            if isinstance(agent, StreamingAgent):
                async for chunk in await agent.stream(request, self._ctx):
                    yield chunk
            else:
                response = await agent.handle(request, self._ctx)
                yield response.content
