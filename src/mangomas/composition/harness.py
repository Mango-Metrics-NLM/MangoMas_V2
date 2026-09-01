"""Harness orchestrator wrapper for telemetry.

Provides an orchestrator subclass that wraps dispatch paths in
harness-level OpenTelemetry spans for observability.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from mangomas.composition.llm import _AgentLLMOverrideCloseMixin
from mangomas.config import HarnessSettings, LoopSettings
from mangomas.core import AgentContext, Orchestrator
from mangomas.core.agent import AgentRequest, AgentResponse
from mangomas.core.loop import AcceptanceFn
from mangomas.telemetry import build_scoped_tracer

logger = logging.getLogger(__name__)

# ── Harness span constants ─────────────────────────────────────────────────────
_HARNESS_SPAN_NAME = "harness.agent_invoke"
_HARNESS_TOPOLOGY_DISPATCH = "dispatch"
_HARNESS_TOPOLOGY_STREAM = "stream"


class _HarnessOrchestrator(_AgentLLMOverrideCloseMixin, Orchestrator):
    """Orchestrator subclass that wraps dispatch paths in a harness-level span.

    Engaged only when ``Settings.harness.enabled`` is ``True``. The parent
    span sits above the existing ``orchestrator.*`` spans so operators can
    filter or alert on agent invocations at the harness layer without
    disturbing the in-orchestrator instrumentation. Because
    ``dispatch_pipeline`` and ``dispatch_fan_out`` delegate through
    ``dispatch``, those topologies inherit the wrap automatically;
    ``stream_dispatch`` does not, so it is wrapped explicitly below.

    Also picks up :class:`~mangomas.composition.llm._AgentLLMOverrideCloseMixin`
    so per-agent ``MODEL_OVERRIDE`` clients (spec-0028 / ADR-0028) are closed
    on :meth:`aclose` the same as the harness-disabled orchestrator.
    """

    def __init__(
        self,
        ctx: AgentContext,
        harness_cfg: HarnessSettings,
        *,
        loop_settings: LoopSettings | None = None,
    ) -> None:
        super().__init__(ctx, loop_settings=loop_settings)
        self._harness_tracer = build_scoped_tracer(
            harness_cfg.metrics_namespace, exporter=harness_cfg.metrics_exporter
        )
        self._harness_cfg = harness_cfg
        logger.debug(
            "Harness orchestrator engaged",
            extra={
                "metrics_namespace": harness_cfg.metrics_namespace,
                "hook_log_level": harness_cfg.hook_log_level,
            },
        )

    async def dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
        *,
        acceptance_fn: AcceptanceFn | None = None,
        max_steps: int | None = None,
    ) -> AgentResponse:
        """Dispatch with harness telemetry span.

        Wraps the dispatch call with a harness-level OpenTelemetry span
        that captures agent name, topology, and message count.
        """
        with self._harness_tracer.start_as_current_span(_HARNESS_SPAN_NAME) as span:
            span.set_attribute("agent.name", agent_name)
            span.set_attribute("harness.topology", _HARNESS_TOPOLOGY_DISPATCH)
            span.set_attribute("messages.count", len(request.messages))
            logger.debug(
                "Harness wrapping dispatch",
                extra={"agent": agent_name, "messages": len(request.messages)},
            )
            return await super().dispatch(
                agent_name,
                request,
                acceptance_fn=acceptance_fn,
                max_steps=max_steps,
            )

    async def stream_dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
    ) -> AsyncIterator[str]:
        """Stream dispatch with harness telemetry span.

        Wraps streaming dispatch so the harness parent span covers token
        emission too.

        ``super().stream_dispatch`` is awaited *here* (not inside the returned
        generator) so :class:`~mangomas.errors.AgentNotFound` still raises
        eagerly, before any streaming begins — unchanged from the base
        behaviour. The span itself is opened only once iteration of the
        returned generator begins, in :meth:`_traced_stream`, which is the fix
        for the previous version of this method: it opened
        ``start_as_current_span`` and then immediately ``return``ed the
        unconsumed async generator from ``super().stream_dispatch`` — the
        ``with`` block exited at iterator *construction*, before a single
        token had flowed, so the span's duration measured "time to validate
        the agent name" rather than the stream.
        """
        inner = await super().stream_dispatch(agent_name, request)
        logger.debug(
            "Harness wrapping stream_dispatch",
            extra={"agent": agent_name, "messages": len(request.messages)},
        )
        return self._traced_stream(agent_name, request, inner)

    async def _traced_stream(
        self,
        agent_name: str,
        request: AgentRequest,
        inner: AsyncIterator[str],
    ) -> AsyncIterator[str]:
        """Yield *inner*'s chunks with the harness span attached only per-``await``.

        Deliberately does **not** use ``start_as_current_span`` around the
        whole body: holding an OTel context across a ``yield`` leaks it into
        the *consumer*'s subsequent spans, because control (and the ambient
        context) returns to the consumer's task at each ``yield`` point — every
        span the consumer creates between chunks would become a child of
        ``harness.agent_invoke`` instead of whatever it should actually be a
        child of. Instead, ``attach``/``detach`` are paired tightly around
        each ``__anext__()`` call, so the harness span is current only while
        this generator is actually running, never while suspended at a
        ``yield``.

        The ``finally`` guarantees the span ends exactly once, regardless of
        how iteration stops: full drain, an exception from *inner*, or early
        abandonment (the consumer calling ``aclose()`` — e.g. on client
        disconnect — throws ``GeneratorExit`` in at the suspended ``yield``).
        Closing *inner* too (when it supports it — the ``StreamingAgent.stream``
        protocol types this as a plain ``AsyncIterator[str]``, so ``aclose()``
        isn't guaranteed even though the built-in implementation, an async
        generator, always has one) propagates that same closure downward so it
        isn't left dangling either.
        """
        span = self._harness_tracer.start_span(_HARNESS_SPAN_NAME)
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("harness.topology", _HARNESS_TOPOLOGY_STREAM)
        span.set_attribute("messages.count", len(request.messages))
        try:
            while True:
                token = otel_context.attach(trace.set_span_in_context(span))
                try:
                    chunk = await inner.__anext__()
                except StopAsyncIteration:
                    break
                finally:
                    otel_context.detach(token)
                yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            # Normal early-close/cancellation, not an application error —
            # record neither an exception nor an ERROR status for these.
            raise
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        finally:
            maybe_aclose = getattr(inner, "aclose", None)
            try:
                if maybe_aclose is not None:
                    await maybe_aclose()
            except Exception:
                # inner is an arbitrary AsyncIterator per the StreamingAgent.stream
                # protocol — a failure while closing it must not suppress span.end()
                # below, or the harness span leaks (never exported).
                logger.warning(
                    "Error closing inner stream while ending the harness span",
                    extra={"agent_name": agent_name},
                    exc_info=True,
                )
            finally:
                span.end()


__all__ = [
    "_HarnessOrchestrator",
]
