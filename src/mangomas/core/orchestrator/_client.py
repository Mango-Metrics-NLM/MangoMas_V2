"""Single orchestrator: routes a request to a named agent and persists the turn."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

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
from mangomas.errors import AgentNotFound, MangomasError, MaxStepsExceeded, StepTimeout

# Telemetry leaf over the OTel API this module already imports
# (``opentelemetry.trace``) — not a domain dependency. The record helpers are
# no-ops until metrics are enabled (ADR-0013), and the orchestrator is the
# single truthful chokepoint for invocation counting (ADR-0026), so every
# dispatch path — HTTP, CLI, workflow nodes, pipeline/fan-out inner steps —
# records unconditionally here.
from mangomas.metrics import (
    record_agent_duration,
    record_agent_error,
    record_agent_invocation,
)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import LoopSettings

logger = logging.getLogger(__name__)

# The AgentRequest field default acts as the "caller stated nothing" sentinel
# in the max_steps precedence chain (spec-0026 R2). Read off the model so the
# two can never desync.
_REQUEST_MAX_STEPS_DEFAULT: int = AgentRequest.model_fields["max_steps"].default


def _outcome_error_code(error: BaseException) -> str:
    """Loggable identifier for a settled fan-out failure — never its content.

    Typed errors surface their stable ``code``; anything else falls back to the
    class name, which is still content-free.
    """
    return error.code if isinstance(error, MangomasError) else type(error).__name__


@dataclass(frozen=True)
class FanOutOutcome:
    """One agent's settled fan-out result: exactly one of response/error is set.

    Defined here rather than in ``agent.py`` deliberately (spec-0027): this is
    an orchestrator-topology result, not a wire DTO — the ``AgentRequest`` /
    ``AgentResponse`` surface stays untouched.
    """

    agent: str
    response: AgentResponse | None = None
    error: BaseException | None = None

    def __post_init__(self) -> None:
        if (self.response is None) == (self.error is None):
            raise ValueError("exactly one of response/error must be set")

    @property
    def ok(self) -> bool:
        """Whether this agent's dispatch succeeded."""
        return self.error is None


class Orchestrator:
    """Registers agents by name and dispatches requests."""

    def __init__(self, ctx: AgentContext, *, loop_settings: LoopSettings | None = None) -> None:
        """*loop_settings* (keyword-only, optional) activates the control-loop
        tunables (spec-0026): a per-step ``asyncio.timeout`` on ``dispatch``'s
        loop and the ``max_steps`` deployment default. ``None`` — the default,
        and what every pre-existing caller passes implicitly — preserves the
        previous behaviour exactly (no step timeout; ``request.max_steps``
        rules). The composition root passes ``Settings.loop``.
        """
        self._ctx = ctx
        self._agents: dict[str, Agent] = {}
        self._closed: bool = False
        self._loop_settings = loop_settings

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

        Records the agent invocation/error/duration instruments around the
        whole call (ADR-0026): ``ok`` + duration on success, ``error`` + the
        error code for any :class:`~mangomas.errors.MangomasError` — so HTTP,
        CLI, workflow-node, and pipeline/fan-out inner-step invocations are
        all uniformly counted (each inner step is its own point).

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
            Precedence (spec-0026 R2): this kwarg > a non-default
            ``request.max_steps`` > ``loop_settings.max_steps`` > the
            ``AgentRequest`` field default.
        """
        if max_steps is not None and max_steps < 1:
            raise ValueError("max_steps must be >= 1")

        start = time.perf_counter()
        try:
            response = await self._dispatch_once(
                agent_name, request, acceptance_fn=acceptance_fn, max_steps=max_steps
            )
        except MangomasError as exc:
            record_agent_invocation(agent_name, "error")
            record_agent_error(agent_name, exc.code)
            raise
        record_agent_invocation(agent_name, "ok")
        record_agent_duration(agent_name, time.perf_counter() - start)
        return response

    async def _dispatch_once(
        self,
        agent_name: str,
        request: AgentRequest,
        *,
        acceptance_fn: AcceptanceFn | None,
        max_steps: int | None,
    ) -> AgentResponse:
        """The dispatch body :meth:`dispatch` wraps with metric emission."""
        if agent_name not in self._agents:
            logger.error(
                "Dispatch failed: agent not found",
                extra={"agent_name": agent_name, "registered": self.list_agents()},
            )
            raise AgentNotFound(agent_name)

        agent = self._agents[agent_name]
        effective_max = self._effective_max_steps(request, max_steps)

        with trace.get_tracer(__name__).start_as_current_span("orchestrator.dispatch") as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("messages.count", len(request.messages))
            span.set_attribute("loop.max_steps", effective_max)
            if self._loop_settings is not None:
                span.set_attribute(
                    "loop.step_timeout_seconds", self._loop_settings.step_timeout_seconds
                )

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
                response = await self._handle_step(agent, step_request)

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

    def _effective_max_steps(self, request: AgentRequest, max_steps: int | None) -> int:
        """Resolve the loop budget by the spec-0026 precedence chain.

        The explicit kwarg wins; a ``request.max_steps`` that differs from the
        field default is caller-stated intent and beats the deployment
        default; ``loop_settings.max_steps`` (env: ``MANGOMAS_LOOP__MAX_STEPS``)
        fills silence; the field default closes the chain. The defaults all
        coincide at 1, so behaviour changes only when an operator raises the
        env var.
        """
        if max_steps is not None:
            return max_steps
        if request.max_steps != _REQUEST_MAX_STEPS_DEFAULT:
            return request.max_steps
        if self._loop_settings is not None:
            return self._loop_settings.max_steps
        return request.max_steps

    async def _handle_step(self, agent: Agent, step_request: AgentRequest) -> AgentResponse:
        """Run one ``agent.handle`` step, under the per-step timeout when wired.

        With ``loop_settings`` unset (a directly constructed orchestrator) the
        step runs unbounded, exactly as before spec-0026. When set, the step
        runs under ``asyncio.timeout(step_timeout_seconds)`` — the stdlib
        structured-concurrency primitive, no hand-rolled timer — and an expiry
        raises the typed :class:`~mangomas.errors.StepTimeout` (504).
        Streaming is deliberately not timeout-wrapped (spec-0026 out of scope).
        """
        if self._loop_settings is None:
            return await agent.handle(step_request, self._ctx)
        seconds = self._loop_settings.step_timeout_seconds
        try:
            async with asyncio.timeout(seconds):
                return await agent.handle(step_request, self._ctx)
        except TimeoutError as exc:
            raise StepTimeout(seconds) from exc

    # ── Multi-agent topologies ────────────────────────────────────────────────

    async def dispatch_pipeline(
        self,
        agent_names: list[str],
        request: AgentRequest,
        *,
        acceptance_fn: AcceptanceFn | None = None,
        max_steps: int | None = None,
    ) -> AgentResponse:
        """Sequential pipeline: each agent's output becomes the next agent's input.

        With the default ``acceptance_fn=None, max_steps=None`` the pipeline
        runs exactly once — byte-identical to the pre-spec-0027 behaviour.
        Providing either keyword engages a **whole-pipeline** acceptance loop
        (spec-0027): the entire pipeline is one loop body, the *final*
        response is judged, and on reject the pipeline re-runs with the
        original first-stage request plus the prior final assistant reply
        appended to its messages (mirroring :meth:`dispatch`'s re-injection
        idiom). This is what a composite workflow ``loop`` body needs and
        cannot be expressed today — a final-stage-only loop is already
        composable via :meth:`dispatch` on the last agent.

        Parameters
        ----------
        agent_names:
            Ordered roster of registered agents; each inner hop is a full
            :meth:`dispatch` (its own metrics point and its own inner loop).
        request:
            The first stage's request. Its ``max_steps`` field governs the
            inner dispatches as ever — it deliberately does **not** join the
            pipeline-loop budget tier, which would double-apply it.
        acceptance_fn:
            Optional sync callable judging the pipeline's **final** response.
            When provided and never satisfied within the budget,
            :class:`~mangomas.errors.MaxStepsExceeded` is raised. When only
            *max_steps* is provided the pipeline iterates with conversational
            re-injection and the last response is returned without raising
            (mirroring :meth:`dispatch`'s no-acceptance semantics).
        max_steps:
            Pipeline-iteration budget; must be >= 1. Precedence (spec-0027):
            this kwarg > ``loop_settings.max_steps`` > the request field
            default.

        In loop mode the returned response's metadata carries the same
        ``"loop"`` block shape :meth:`dispatch` uses
        (``{"steps_taken", "accepted"}``) at the **pipeline** level; the inner
        dispatches' own responses keep their metadata untouched.

        Raises ``ValueError`` when *agent_names* is empty or *max_steps* < 1.
        Raises :class:`~mangomas.errors.AgentNotFound` if any name is unregistered.
        """
        if not agent_names:
            raise ValueError("agent_names must be non-empty")
        if max_steps is not None and max_steps < 1:
            raise ValueError("max_steps must be >= 1")

        with trace.get_tracer(__name__).start_as_current_span(
            "orchestrator.dispatch_pipeline"
        ) as span:
            span.set_attribute("topology", "pipeline")
            span.set_attribute("agent_count", len(agent_names))

            if acceptance_fn is None and max_steps is None:
                # Single pass — the pre-spec-0027 contract, byte-identical.
                return await self._pipeline_pass(agent_names, request)

            effective_max = self._pipeline_effective_max_steps(max_steps)
            span.set_attribute("loop.max_steps", effective_max)

            current_messages = list(request.messages)
            response: AgentResponse | None = None
            accepted = False
            steps_taken = 0

            for _ in range(effective_max):
                steps_taken += 1
                first_stage_request = AgentRequest(
                    messages=current_messages,
                    metadata=request.metadata,
                    max_steps=request.max_steps,
                )
                response = await self._pipeline_pass(agent_names, first_stage_request)

                if acceptance_fn is not None:
                    if acceptance_fn(response):
                        accepted = True
                        logger.debug(
                            "Pipeline acceptance criteria met at iteration %d", steps_taken
                        )
                        break
                    # Re-inject the prior FINAL assistant reply for the next pass.
                    current_messages = [
                        *current_messages,
                        Message(role="assistant", content=response.content),
                    ]
                elif effective_max > 1:
                    # Multi-step without acceptance: conversational continuity.
                    current_messages = [
                        *current_messages,
                        Message(role="assistant", content=response.content),
                    ]

            if acceptance_fn is not None and not accepted:
                raise MaxStepsExceeded(effective_max)

            assert response is not None  # noqa: S101 — guaranteed: effective_max >= 1

            # Pipeline-level loop telemetry; the inner dispatches keep theirs.
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

        return response

    async def _pipeline_pass(self, agent_names: list[str], request: AgentRequest) -> AgentResponse:
        """One full pipeline pass — the exact pre-spec-0027 threading.

        No metrics of its own: each inner hop is a full :meth:`dispatch`, so
        every step is already its own invocation/error/duration point
        (ADR-0026); a separate pipeline-level instrument would only grow
        instrument cardinality without adding information.
        """
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

    def _pipeline_effective_max_steps(self, max_steps: int | None) -> int:
        """Resolve the pipeline-loop budget (spec-0027 precedence).

        The explicit kwarg wins; ``loop_settings.max_steps`` fills silence; the
        ``AgentRequest`` field default closes the chain. ``request.max_steps``
        is deliberately absent from this chain — it already governs each inner
        dispatch's own budget, and reading it here too would double-apply it.
        """
        if max_steps is not None:
            return max_steps
        if self._loop_settings is not None:
            return self._loop_settings.max_steps
        return _REQUEST_MAX_STEPS_DEFAULT

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

    async def dispatch_fan_out_settled(
        self, agent_names: list[str], request: AgentRequest
    ) -> list[FanOutOutcome]:
        """Fan-out that settles every branch: per-agent outcomes, in roster order.

        The additive sibling of :meth:`dispatch_fan_out` (spec-0027): where the
        fail-fast method discards successful siblings the moment one agent
        raises — its established contract, deliberately unchanged — this one
        gathers with ``return_exceptions=True`` and maps each result to a
        :class:`FanOutOutcome` (exactly one of ``response``/``error`` set),
        preserving the declaration order ``asyncio.gather`` guarantees.

        Each inner hop is a full :meth:`dispatch`, so the failed agents' error
        metrics are already recorded per-agent (ADR-0026) — no extra
        instrument here.

        Raises ``ValueError`` when *agent_names* is empty, mirroring the sibling.
        """
        if not agent_names:
            raise ValueError("agent_names must be non-empty")

        with trace.get_tracer(__name__).start_as_current_span(
            "orchestrator.dispatch_fan_out_settled"
        ) as span:
            span.set_attribute("topology", "fan_out")
            span.set_attribute("agent_count", len(agent_names))

            settled: list[AgentResponse | BaseException] = await asyncio.gather(
                *(self.dispatch(name, request) for name in agent_names),
                return_exceptions=True,
            )
            outcomes = [
                FanOutOutcome(agent=name, error=result)
                if isinstance(result, BaseException)
                else FanOutOutcome(agent=name, response=result)
                for name, result in zip(agent_names, settled, strict=True)
            ]
            span.set_attribute(
                "fan_out.failed_count", sum(1 for outcome in outcomes if not outcome.ok)
            )

        # Names + error codes only — never response content.
        logger.debug(
            "Settled fan-out complete: %s",
            [
                (
                    outcome.agent,
                    "ok" if outcome.error is None else _outcome_error_code(outcome.error),
                )
                for outcome in outcomes
            ],
        )
        return outcomes

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
            # Pre-stream failure: record the error metrics here (ADR-0026) —
            # the generator below never starts, so its own error path cannot.
            exc = AgentNotFound(agent_name)
            record_agent_invocation(agent_name, "error")
            record_agent_error(agent_name, exc.code)
            raise exc

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

        Metrics follow the same symmetry (spec-0025 rule, seam moved here by
        ADR-0026): full drain ⇔ persisted ⇔ counted. The ok invocation +
        duration are recorded only after the drain-and-persist path completes;
        a :class:`~mangomas.errors.MangomasError` mid-drain (or while
        persisting) records the error metrics before re-raising; abandonment
        (``GeneratorExit``) unwinds past both and records nothing.
        """
        start = time.perf_counter()
        with trace.get_tracer(__name__).start_as_current_span(
            "orchestrator.stream_dispatch"
        ) as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("messages.count", len(request.messages))

            degraded = not isinstance(agent, StreamingAgent)
            span.set_attribute("stream.degraded", degraded)

            try:
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
            except MangomasError as exc:
                record_agent_invocation(agent_name, "error")
                record_agent_error(agent_name, exc.code)
                raise
            record_agent_invocation(agent_name, "ok")
            record_agent_duration(agent_name, time.perf_counter() - start)
