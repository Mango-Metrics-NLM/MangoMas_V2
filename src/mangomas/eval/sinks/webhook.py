"""Webhook sink — POST the report (and gate verdict) JSON to a URL.

Useful for CI notifications. The payload is built by the shared
:func:`mangomas.eval._serialize.report_payload`, so it is structurally identical
to the ``json_file`` sink's. ``httpx`` is a core dependency, so this needs no
optional extra. The client is constructed per ``emit`` and closed in a
``finally``; a non-2xx response raises (caught by the CLI's per-sink fault
isolation).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, DEFAULT_EVAL_WEBHOOK_TIMEOUT_SECONDS
from mangomas.errors import PersistenceError
from mangomas.eval._options import require_non_negative_float, require_str
from mangomas.eval._serialize import report_payload
from mangomas.eval.sink import Sink
from mangomas.eval.sink_registry import sink_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport

logger = logging.getLogger(__name__)


class WebhookSink:
    """POST the eval report as JSON to a configured URL."""

    name = "webhook"

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float = DEFAULT_EVAL_WEBHOOK_TIMEOUT_SECONDS,
    ) -> None:
        self._url = url
        self._timeout = timeout_seconds
        # Slack / Teams / Discord webhook URLs carry their secret in the path;
        # only the origin is ever logged or surfaced in an error message.
        parts = urlsplit(url)
        self._origin = f"{parts.scheme}://{parts.netloc}"

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        payload = report_payload(report, gate_result=gate_result)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # ``str(exc)`` embeds the full URL; keep it out of the message the
            # CLI echoes and the sink-failure log line.
            raise PersistenceError(
                f"webhook sink {self._origin} returned HTTP {exc.response.status_code}",
                detail=exc.response.text[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc
        except httpx.HTTPError as exc:
            raise PersistenceError(
                f"webhook sink {self._origin} request failed",
                detail=type(exc).__name__,
            ) from exc
        logger.info(
            "Eval report posted to webhook",
            extra={"event": "eval_sink_webhook", "origin": self._origin},
        )


def _webhook_factory(options: dict[str, Any]) -> Sink:
    url = require_str(options, "url", owner="webhook sink")
    timeout = require_non_negative_float(
        options.get("timeout_seconds", DEFAULT_EVAL_WEBHOOK_TIMEOUT_SECONDS),
        owner="webhook sink",
        field="timeout_seconds",
    )
    return WebhookSink(url=url, timeout_seconds=timeout)


sink_registry.register("webhook", _webhook_factory)
