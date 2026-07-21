"""Synchronous bridge target: drive Mango-Mas as a black box from the
``ianshank/Agents`` eval harness (the ``eval-harness`` CLI).

The Agents ``callable`` target dynamic-imports ``module:function`` and, with
``pass_item: false`` (the default), invokes it **synchronously** as
``fn(item.inputs)`` — storing the raw return value as the graded prediction.
This module is that function. :func:`predict` performs a synchronous HTTP POST
to a running Mango-Mas API (started via
``uvicorn mangomas.api.app:create_app --factory``) and returns the response
``content`` string.

It deliberately imports **nothing** from ``mangomas``: the target is a genuine
out-of-process black box. That also sidesteps the event-loop-per-row trap of an
in-process ``asyncio.run`` bridge — Mango-Mas's LM Studio client binds its async
httpx pool to the first event loop, so a per-row ``asyncio.run`` dies with
"Event loop is closed" on the second row. HTTP avoids the loop entirely.

Usage (in the Agents ``EvalConfig`` YAML)::

    target:
      type: callable
      params:
        path: "mango_bridge:predict"
        pass_item: false

Environment (all optional; every tunable has a named default):
    ``MANGO_BASE_URL``   Base URL of the running Mango-Mas API
                         (default ``http://localhost:8000``).
    ``MANGO_AGENT``      Default agent when a row omits ``inputs.agent``
                         (default ``chat``).
    ``MANGO_TIMEOUT``    Per-request timeout in seconds (default ``60``).
    ``MANGO_MAX_STEPS``  Default ``max_steps`` when a row omits it (default
                         ``1``; mirrors ``MANGOMAS_LOOP__MAX_STEPS``).
"""

from __future__ import annotations

import atexit
import logging
import os
import time
from functools import lru_cache
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# --- Defaults (env-overridable; no bare literals in the request path) ---------
_DEFAULT_BASE_URL = "http://localhost:8000"
_DEFAULT_AGENT = "chat"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_STEPS = 1  # mirrors Mango-Mas MANGOMAS_LOOP__MAX_STEPS default

# Ordered precedence for synthesising a user turn from a flat ``inputs`` row.
_USER_TEXT_FIELDS = ("question", "prompt", "input")
# Mango-Mas invoke route — a fixed structural piece of the API contract.
_INVOKE_PATH = "/agents/{agent}/invoke"
# Cap on the response body captured in an error log line.
_ERROR_BODY_TRUNCATE = 500


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    """Process-wide singleton client so the keep-alive pool is reused per run.

    Built lazily on first call (not at import) so the module imports cleanly
    inside the harness before any environment is configured.
    """
    base_url = os.environ.get("MANGO_BASE_URL", _DEFAULT_BASE_URL)
    timeout = float(os.environ.get("MANGO_TIMEOUT", _DEFAULT_TIMEOUT_SECONDS))
    logger.info(
        "Bridge HTTP client configured",
        extra={"event": "bridge_client_configured", "base_url": base_url, "timeout_s": timeout},
    )
    return httpx.Client(base_url=base_url, timeout=timeout)


def close_client() -> None:
    """Close and drop the cached client (idempotent).

    Registered via :func:`atexit` so one-shot CLI runs release sockets cleanly;
    also called by the test suite between cases to avoid ``ResourceWarning``.
    """
    if _client.cache_info().currsize:
        _client().close()
        _client.cache_clear()


atexit.register(close_client)


def _default_max_steps() -> int:
    return int(os.environ.get("MANGO_MAX_STEPS", _DEFAULT_MAX_STEPS))


def _validated_turn(entry: Any) -> dict[str, str]:
    """Return a ``{role, content}`` turn, failing loud on off-contract entries."""
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("role"), str)
        or not isinstance(entry.get("content"), str)
    ):
        raise ValueError(f"malformed message in inputs.messages: {entry!r}")
    return {"role": entry["role"], "content": entry["content"]}


def to_messages(inputs: dict[str, Any]) -> list[dict[str, str]]:
    """Map an Agents ``inputs`` dict to Mango-Mas ``messages``.

    Precedence:

    1. If ``inputs['messages']`` is present, it must be a non-empty list of
       ``{role, content}`` objects — passed through verbatim (multi-turn rows
       round-trip losslessly). A present-but-malformed value fails loud rather
       than silently degrading, matching the surrounding fail-closed posture.
    2. Otherwise synthesise: an optional ``system`` message, then a single
       ``user`` message taken from the first *present* of
       ``question`` / ``prompt`` / ``input`` (see :data:`_USER_TEXT_FIELDS`),
       else the empty string.
    """
    raw = inputs.get("messages")
    if raw is not None:
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"inputs.messages must be a non-empty list; got {raw!r}")
        return [_validated_turn(entry) for entry in raw]

    messages: list[dict[str, str]] = []
    system = inputs.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    user = next((str(inputs[key]) for key in _USER_TEXT_FIELDS if key in inputs), "")
    messages.append({"role": "user", "content": user})
    return messages


def predict(inputs: dict[str, Any]) -> str:
    """Agents ``callable`` target entry point — return the graded prediction.

    Raises on a non-2xx response or an off-contract body so the harness records
    the row as *errored* (``TargetOutput.error``) rather than silently scoring a
    bogus string. Emits structured logs for request outcome, latency, and agent.
    """
    agent = str(inputs.get("agent") or os.environ.get("MANGO_AGENT", _DEFAULT_AGENT))
    metadata = inputs.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"inputs.metadata must be an object; got {type(metadata).__name__}")
    messages = to_messages(inputs)
    payload: dict[str, Any] = {
        "messages": messages,
        "metadata": dict(metadata),
        "max_steps": int(inputs.get("max_steps", _default_max_steps())),
    }
    started = time.perf_counter()
    try:
        response = _client().post(_INVOKE_PATH.format(agent=agent), json=payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Bridge invoke failed",
            extra={
                "event": "bridge_invoke_error",
                "agent": agent,
                "status": exc.response.status_code,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "body": exc.response.text[:_ERROR_BODY_TRUNCATE],
            },
        )
        raise
    latency_ms = (time.perf_counter() - started) * 1000
    data = response.json()
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        raise ValueError(f"unexpected Mango-Mas response (no string 'content'): {data!r}")
    logger.debug(
        "Bridge invoke ok",
        extra={
            "event": "bridge_invoke",
            "agent": agent,
            "status": response.status_code,
            "latency_ms": latency_ms,
            "messages": len(messages),
        },
    )
    return content
