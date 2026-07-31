"""Langfuse sink — publish eval results to Langfuse (optional extra).

Langfuse is an **optional** dependency: install with ``pip install
'mangomas[langfuse]'``. The SDK is lazy-imported so the package stays usable
(and importable) without it — exactly like the Vertex / Chroma adapters.

Credentials are read from the environment by the Langfuse SDK
(``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST``); nothing
is hard-coded. Because the CLI is a short-lived process, ``emit`` calls
``client.flush()`` before returning — without it Langfuse's background batching
would drop the events on interpreter exit. All SDK calls run under
``asyncio.to_thread`` so the synchronous client never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from mangomas.eval._langfuse import build_langfuse_client
from mangomas.eval.sink import Sink
from mangomas.eval.sink_registry import sink_registry
from mangomas.telemetry import get_tracer

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)

_OWNER = "langfuse sink"


class LangfuseSink:
    """Emit a trace + a ``mean_score`` (and gate verdict) to Langfuse."""

    name = "langfuse"

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        # Resolve the SDK *and* construct the client at init so any
        # misconfiguration (missing extra, bad options) surfaces immediately
        # (exit 2) rather than after a full eval run.
        opts = options or {}
        # ``per_row`` is our own option — read it before the shared client
        # constructor drops it from the kwargs forwarded to the SDK ctor.
        # Default False preserves the aggregate-only behaviour (one trace +
        # one mean_score).
        self._per_row = bool(opts.get("per_row", False))
        self._client = build_langfuse_client(opts, owner=_OWNER, drop_keys=("per_row",))

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        with _tracer.start_as_current_span("eval.sink.langfuse") as span:
            span.set_attribute("eval.scorer", report.scorer)
            span.set_attribute("eval.agent", report.agent_name)
            await asyncio.to_thread(self._publish, report, gate_result)

    def _publish(
        self,
        report: EvalReport,
        gate_result: GateResult | None,
    ) -> None:
        client = self._client
        try:
            metadata: dict[str, Any] = {
                "scorer": report.scorer,
                "agent": report.agent_name,
                "dataset_size": report.dataset_size,
                "passed": report.passed,
                "failed": report.failed,
                "errored": report.errored,
            }
            if gate_result is not None:
                metadata["gate_passed"] = gate_result.passed
                metadata["gate_reasons"] = gate_result.reasons
            trace = client.trace(name="mangomas-eval", metadata=metadata)
            client.score(trace_id=trace.id, name="mean_score", value=report.mean_score)
            if self._per_row:
                for row in report.rows:
                    row_trace = client.trace(
                        name="mangomas-eval-row",
                        metadata={
                            "row_id": row.row_id,
                            "passed": row.passed,
                            "expected": row.expected,
                            "prediction": row.prediction,
                        },
                    )
                    client.score(trace_id=row_trace.id, name="row_score", value=row.score)
        finally:
            # Mandatory for short-lived CLI processes — otherwise batched
            # events are dropped on interpreter exit.
            client.flush()
        logger.info(
            "Eval report published to Langfuse",
            extra={"event": "eval_sink_langfuse", "scorer": report.scorer},
        )


def _langfuse_factory(options: dict[str, Any]) -> Sink:
    return LangfuseSink(options=options)


sink_registry.register("langfuse", _langfuse_factory)
