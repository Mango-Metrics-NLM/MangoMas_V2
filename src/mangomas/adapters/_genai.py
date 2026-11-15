"""Shared async/sync lifecycle helpers for isolated ``google.genai`` clients."""

from __future__ import annotations

import inspect
from typing import Any


async def _invoke_close(close_fn: Any) -> None:
    if callable(close_fn):
        result = close_fn()
        if inspect.isawaitable(result):
            await result


async def close_client_handle(handle: Any) -> None:
    """Invoke async ``aclose()`` or sync ``close()`` on an owned handle."""
    close = getattr(handle, "aclose", None) or getattr(handle, "close", None)
    await _invoke_close(close)


async def close_genai_client(client: Any) -> None:
    """Close both async (``client.aio``) and sync handles on ``google.genai.Client``."""
    try:
        aio = getattr(client, "aio", None)
        if aio is not None:
            await _invoke_close(getattr(aio, "aclose", None))
    finally:
        await _invoke_close(getattr(client, "close", None))
