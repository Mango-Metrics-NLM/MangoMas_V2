"""Optional additive GenAI span alias (Development semconv; default-off).

Live traces stay ``orchestrator.*`` / ``harness.agent_invoke``. This helper
opens ``gen_ai.invoke_agent`` only when the caller passes ``enabled=True``
(``MANGOMAS_SIGNAL__GENAI_SPANS``). It must not install a TracerProvider —
callers reuse the process-global tracer from ``opentelemetry.trace``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mangomas.cognitive.constants import (
    GENAI_ATTR_AGENT_NAME,
    GENAI_ATTR_OPERATION_NAME,
    GENAI_ATTR_SEMCONV_STATUS,
    GENAI_ATTR_SYSTEM,
    GENAI_INVOKE_AGENT_SPAN,
    GENAI_OPERATION_INVOKE_AGENT,
    GENAI_SEMCONV_STATUS,
    GENAI_SYSTEM_NAME,
)

logger = logging.getLogger(__name__)


@contextmanager
def genai_invoke_agent_span(*, enabled: bool, agent_name: str) -> Iterator[Any]:
    """Open a Development-semconv ``gen_ai.invoke_agent`` span, or yield ``None``.

    When *enabled* is false this is a no-op (no tracer lookup, no span). The
    span is current for the duration of the ``with`` block so lineage join
    keys can read ``otel-trace:`` from the active context.
    """
    if not enabled:
        yield None
        return
    from opentelemetry import trace  # noqa: PLC0415

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(GENAI_INVOKE_AGENT_SPAN) as span:
        span.set_attribute(GENAI_ATTR_OPERATION_NAME, GENAI_OPERATION_INVOKE_AGENT)
        span.set_attribute(GENAI_ATTR_AGENT_NAME, agent_name)
        span.set_attribute(GENAI_ATTR_SYSTEM, GENAI_SYSTEM_NAME)
        span.set_attribute(GENAI_ATTR_SEMCONV_STATUS, GENAI_SEMCONV_STATUS)
        logger.debug(
            "GenAI invoke_agent alias opened",
            extra={"event": "genai_span_opened", "agent": agent_name},
        )
        yield span
