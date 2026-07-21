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

Environment:
    ``MANGO_BASE_URL``  Base URL of the running Mango-Mas API
                        (default ``http://localhost:8000``).
    ``MANGO_AGENT``     Default agent name when a row omits ``inputs.agent``
                        (default ``chat``).
    ``MANGO_TIMEOUT``   Per-request timeout in seconds (default ``60``).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx

_DEFAULT_BASE_URL = "http://localhost:8000"
_DEFAULT_AGENT = "chat"
_DEFAULT_TIMEOUT = 60.0


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    """Process-wide singleton client so the keep-alive pool is reused per run.

    Built lazily on first call (not at import) so the module imports cleanly
    inside the harness before any environment is configured. Tests reset it via
    ``_client.cache_clear()``.
    """
    base_url = os.environ.get("MANGO_BASE_URL", _DEFAULT_BASE_URL)
    timeout = float(os.environ.get("MANGO_TIMEOUT", _DEFAULT_TIMEOUT))
    return httpx.Client(base_url=base_url, timeout=timeout)


def to_messages(inputs: dict[str, Any]) -> list[dict[str, str]]:
    """Map an Agents ``inputs`` dict to Mango-Mas ``messages``.

    Precedence:

    1. If ``inputs['messages']`` is already a non-empty role/content list, pass
       it through verbatim. Native multi-turn rows round-trip losslessly — this
       is how :mod:`convert_dataset` nests Mango-Mas messages under
       ``inputs.messages``.
    2. Otherwise synthesise: an optional ``system`` message, then a single
       ``user`` message taken from the first present of ``question`` /
       ``prompt`` / ``input``, else the empty string.
    """
    raw = inputs.get("messages")
    if isinstance(raw, list) and raw:
        turns: list[dict[str, str]] = []
        for m in raw:
            if not isinstance(m, dict) or "role" not in m or "content" not in m:
                raise ValueError(f"malformed message in inputs.messages: {m!r}")
            turns.append({"role": str(m["role"]), "content": str(m["content"])})
        return turns

    messages: list[dict[str, str]] = []
    system = inputs.get("system")
    if system:
        messages.append({"role": "system", "content": str(system)})
    user = inputs.get("question") or inputs.get("prompt") or inputs.get("input") or ""
    messages.append({"role": "user", "content": str(user)})
    return messages


def predict(inputs: dict[str, Any]) -> str:
    """Agents ``callable`` target entry point — return the graded prediction.

    Raises on a non-2xx response so the harness records the row as *errored*
    (``TargetOutput.error``) rather than silently scoring an empty string. Keep
    ``errored == 0`` asserted in CI so a down sidecar fails the run loudly.
    """
    agent = str(inputs.get("agent") or os.environ.get("MANGO_AGENT", _DEFAULT_AGENT))
    payload: dict[str, Any] = {
        "messages": to_messages(inputs),
        "metadata": dict(inputs.get("metadata") or {}),
        "max_steps": int(inputs.get("max_steps", 1)),
    }
    response = _client().post(f"/agents/{agent}/invoke", json=payload)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or "content" not in data:
        raise ValueError(f"unexpected Mango-Mas response (missing 'content'): {data!r}")
    return str(data["content"])
