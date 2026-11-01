"""In-process OpenAI-compatible stub for hermetic consumer probes (spec-0041).

Serves the four routes ``LMStudioClient`` and the LM Studio embedding client
speak — ``GET /v1/models``, ``POST /v1/chat/completions`` (JSON and SSE) and
``POST /v1/embeddings`` — as an ASGI app, so a probe injects it through
``httpx.ASGITransport`` and never opens a socket.

Every completion is ``"[<model>] <last user message>"``. Echoing the model id
is what makes routing observable: a profile that resolved the wrong client
answers with the wrong prefix. Real LM Studio serves an unknown id from
whatever model is loaded, so it cannot give that signal; the stub can.
``FAIL_MODEL`` answers 400, standing in for an upstream that rejects the call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

FAIL_MODEL = "stub-unloaded-model"
EMBEDDING_DIMENSIONS = 8
_UPSTREAM_REJECTED_STATUS = 400
_SSE_DONE = "data: [DONE]\n\n"


def reply_for(model: str, messages: list[dict[str, Any]]) -> str:
    """Return the deterministic completion for *model* answering *messages*."""
    last_user = next(
        (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    return f"[{model}] {last_user}"


def embed_text(text: str) -> list[float]:
    """Map *text* to a fixed-width vector in ``[0, 1]``, stable across runs."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [byte / 255 for byte in digest[:EMBEDDING_DIMENSIONS]]


def _completion_body(model: str, text: str) -> dict[str, Any]:
    return {
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
    }


async def _sse_frames(text: str) -> AsyncIterator[str]:
    # One frame per word keeps the token stream multi-chunk, like a real server.
    for index, word in enumerate(text.split(" ")):
        token = word if index == 0 else f" {word}"
        yield f"data: {json.dumps({'choices': [{'delta': {'content': token}}]})}\n\n"
    yield _SSE_DONE


def build_stub_app(models: list[str]) -> FastAPI:
    """Build the stub; *models* is the roster ``GET /v1/models`` reports."""
    app = FastAPI(title="openai-compat-stub")

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": m, "object": "model"} for m in models]}

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        body = await request.json()
        model = str(body.get("model", ""))
        if model == FAIL_MODEL:
            return JSONResponse(
                {"error": {"message": f"model {model!r} is not loaded"}},
                status_code=_UPSTREAM_REJECTED_STATUS,
            )
        text = reply_for(model, list(body.get("messages", [])))
        if body.get("stream"):
            return StreamingResponse(_sse_frames(text), media_type="text/event-stream")
        return JSONResponse(_completion_body(model, text))

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> dict[str, Any]:
        body = await request.json()
        raw = body.get("input", [])
        inputs = [raw] if isinstance(raw, str) else list(raw)
        data = [
            {"object": "embedding", "index": i, "embedding": embed_text(str(text))}
            for i, text in enumerate(inputs)
        ]
        return {"object": "list", "model": body.get("model", ""), "data": data}

    return app
