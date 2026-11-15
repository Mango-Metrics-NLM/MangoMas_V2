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
import math
import os
import re
import time
from collections.abc import Mapping
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
    raw_timeout = os.environ.get("MANGO_TIMEOUT", str(_DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise ValueError(
            f"MANGO_TIMEOUT must be a finite positive float; got {raw_timeout!r}"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError(f"MANGO_TIMEOUT must be a finite positive float; got {raw_timeout!r}")
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


def _parse_positive_int(raw: object, *, name: str) -> int:
    """Parse *raw* as a positive integer (``>= 1``), rejecting ``bool`` and ``None``."""
    if isinstance(raw, bool):
        logger.debug("Rejected boolean value for positive integer field", extra={"field": name})
        raise ValueError(f"{name} must be a positive integer >= 1; got {raw!r}")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw.strip(), 10)
        except ValueError as exc:
            logger.debug(
                "Rejected non-numeric string for positive integer field", extra={"field": name}
            )
            raise ValueError(f"{name} must be a positive integer >= 1; got {raw!r}") from exc
    else:
        logger.debug(
            "Rejected invalid type for positive integer field",
            extra={"field": name, "actual_type": type(raw).__name__},
        )
        raise ValueError(f"{name} must be a positive integer >= 1; got {raw!r}")
    if value < 1:
        logger.debug("Rejected non-positive integer value", extra={"field": name, "value": value})
        raise ValueError(f"{name} must be a positive integer >= 1; got {value!r}")
    return value


def _default_max_steps() -> int:
    raw = os.environ.get("MANGO_MAX_STEPS")
    return _DEFAULT_MAX_STEPS if raw is None else _parse_positive_int(raw, name="MANGO_MAX_STEPS")


def _require_mapping(value: Any, *, field: str, message: str) -> Mapping[str, Any]:
    """Ensure *value* is a ``Mapping``, emitting a type-only debug record on failure."""
    if not isinstance(value, Mapping):
        logger.debug(
            "Rejected non-mapping field",
            extra={
                "event": "bridge_invalid_field",
                "field": field,
                "actual_type": type(value).__name__,
            },
        )
        raise ValueError(f"{message}; got {type(value).__name__}")
    return value


def _validated_turn(entry: Any) -> dict[str, str]:
    """Return a ``{role, content}`` turn, failing loud on off-contract entries."""
    if (
        not isinstance(entry, Mapping)
        or not isinstance(entry.get("role"), str)
        or not isinstance(entry.get("content"), str)
    ):
        logger.debug(
            "Rejected malformed message entry",
            extra={
                "event": "bridge_invalid_field",
                "field": "inputs.messages[]",
                "actual_type": type(entry).__name__,
            },
        )
        raise ValueError(
            "malformed message in inputs.messages: expected role/content strings, "
            f"got {type(entry).__name__}"
        )
    return {"role": entry["role"], "content": entry["content"]}


def _validated_optional_str(inputs: Mapping[str, Any], key: str) -> str | None:
    """Return ``inputs.get(key)`` if absent/``None`` or ``str``, raising on other types."""
    value = inputs.get(key)
    if value is not None and not isinstance(value, str):
        logger.debug(
            "Rejected non-string field",
            extra={
                "event": "bridge_invalid_field",
                "field": f"inputs.{key}",
                "actual_type": type(value).__name__,
            },
        )
        raise ValueError(f"inputs.{key} must be a string; got {type(value).__name__}")
    return value


def to_messages(inputs: Mapping[str, Any]) -> list[dict[str, str]]:
    """Map an Agents ``inputs`` mapping to Mango-Mas ``messages``.

    Precedence:

    1. If ``inputs['messages']`` is present, it must be a non-empty list of
       ``{role, content}`` objects — passed through verbatim (multi-turn rows
       round-trip losslessly). A present-but-malformed value fails loud rather
       than silently degrading, matching the surrounding fail-closed posture.
    2. Otherwise synthesise: an optional non-empty ``system`` string, then a
       single ``user`` message taken from the first non-``None`` string of
       ``question`` / ``prompt`` / ``input`` (see :data:`_USER_TEXT_FIELDS`),
       else the empty string. Present non-string values fail loud across all
       synthesised fields rather than silently coercing (including rejecting
       ``None`` coercion to ``"None"``).
    """
    inputs = _require_mapping(inputs, field="inputs", message="inputs must be a mapping")
    raw = inputs.get("messages")
    if raw is not None:
        if not isinstance(raw, list) or not raw:
            logger.debug(
                "Rejected malformed messages list",
                extra={
                    "event": "bridge_invalid_field",
                    "field": "inputs.messages",
                    "actual_type": type(raw).__name__,
                },
            )
            raise ValueError(f"inputs.messages must be a non-empty list; got {type(raw).__name__}")
        return [_validated_turn(entry) for entry in raw]

    messages: list[dict[str, str]] = []
    system = _validated_optional_str(inputs, "system")
    if system:
        messages.append({"role": "system", "content": system})
    user_values = [_validated_optional_str(inputs, key) for key in _USER_TEXT_FIELDS]
    user = next((val for val in user_values if val is not None), "")
    messages.append({"role": "user", "content": user})
    return messages


def predict(inputs: Mapping[str, Any]) -> str:
    """Agents ``callable`` target entry point — return the graded prediction.

    Raises on a non-2xx response or an off-contract body so the harness records
    the row as *errored* (``TargetOutput.error``) rather than silently scoring a
    bogus string. Emits structured logs for request outcome, latency, and agent.
    """
    inputs = _require_mapping(inputs, field="inputs", message="inputs must be a mapping")
    override_agent = _validated_optional_str(inputs, "agent")
    agent = (
        os.environ.get("MANGO_AGENT", _DEFAULT_AGENT) if override_agent is None else override_agent
    )
    if not re.fullmatch(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z", agent):
        raise ValueError(f"invalid agent name: {agent!r}")
    raw_metadata = inputs.get("metadata")
    metadata = (
        {}
        if raw_metadata is None
        else _require_mapping(
            raw_metadata,
            field="inputs.metadata",
            message="inputs.metadata must be an object",
        )
    )
    max_steps = (
        _parse_positive_int(inputs["max_steps"], name="inputs.max_steps")
        if "max_steps" in inputs
        else _default_max_steps()
    )
    messages = to_messages(inputs)
    payload: dict[str, Any] = {
        "messages": messages,
        "metadata": dict(metadata),
        "max_steps": max_steps,
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
