"""Message and stream helpers for the Vertex adapter.

Pure transforms. ``VertexClient`` calls them through the re-exports on
``_client`` so a monkeypatch of that module still sees the same functions.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any, Final

from mangomas.adapters.llm.vertex._sdk import VertexError
from mangomas.core.agent import Message
from mangomas.utils.exc import truncated_exc_detail

_PING_PROMPT: Final[str] = "ping"


def _vertex_role_for(role: str) -> str:
    """Map Mango-Mas roles to Vertex Gemini roles.

    Gemini supports ``user`` and ``model``; everything else folds into ``user``.
    """
    return "model" if role == "assistant" else "user"


def _annotate(message: Message) -> str:
    """Prefix non-user/assistant roles so context is preserved across the fold."""
    if message.role in ("user", "assistant"):
        return message.content
    return f"[{message.role}] {message.content}"


def _extract_text(response: Any) -> str:
    """Extract text from a ``GenerationResponse``-like object.

    The SDK's ``.text`` property raises ``ValueError`` when the response has no
    usable candidate. ``getattr`` only absorbs ``AttributeError``, so that case
    is mapped to :class:`VertexError` here.
    """
    try:
        text = getattr(response, "text", None)
    except ValueError as exc:
        raise VertexError(
            "Vertex AI returned no usable candidate",
            detail=truncated_exc_detail(exc),
        ) from exc
    if text is None:
        return ""
    return str(text)


async def _aiter(stream_result: Any) -> AsyncIterable[Any]:
    """Normalise ``stream_result`` to an async iterable.

    A real ``generate_content_async(stream=True)`` result is async-iterable.
    Some fakes hand back a plain iterable instead.
    """
    if hasattr(stream_result, "__aiter__"):
        async for item in stream_result:
            yield item
        return
    for item in stream_result:  # pragma: no cover - sync-iterable fallback
        yield item
