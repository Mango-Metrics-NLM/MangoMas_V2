"""LM Studio E2E — scenario 7: a streamed turn persists (spec-0025).

The live confirmation of tier-1 flow I1. The in-process flow proves the wiring
against a fake; this proves it against a real model's token stream, where
chunk boundaries, timing and count are all outside the test's control.

Hardware contract (spec-0029 R2): the oracle is structural — a turn exists,
under the right agent, whose content is what the stream actually delivered.
Nothing here depends on how many chunks the model emitted, how fast, or what
it said.

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from mangomas.core import Orchestrator
from tests.constants import ASGI_TEST_BASE_URL
from tests.lmstudio.conftest import parse_sse_data

logger = logging.getLogger(__name__)

_PROMPT = "Reply with one short sentence about testing."


@pytest.mark.lmstudio
async def test_streamed_turn_is_persisted(
    lmstudio_app: FastAPI,
    lmstudio_orchestrator: Orchestrator,
    lmstudio_client_timeout: float,
) -> None:
    """A fully drained live stream leaves exactly one persisted ``chat`` turn."""
    transport = httpx.ASGITransport(app=lmstudio_app)
    tokens: list[str] = []
    done_seen = False

    async with (
        httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL) as client,
        client.stream(
            "POST",
            "/agents/chat/stream",
            json={"messages": [{"role": "user", "content": _PROMPT}]},
            timeout=lmstudio_client_timeout,
        ) as response,
    ):
        assert response.status_code == 200, response.reason_phrase
        async for line in response.aiter_lines():
            frame = parse_sse_data(line)
            if frame is None:
                continue
            if frame.get("event") == "token":
                tokens.append(frame["data"]["content"])
            elif frame.get("event") == "done":
                done_seen = True
                break

    assert tokens, "expected at least one token frame"
    assert done_seen, "the stream must reach its done sentinel for the turn to persist"

    repo = lmstudio_orchestrator.context.repo
    assert repo is not None, "the E2E orchestrator must wire a repository"
    history = await repo.list_turns(limit=5)
    logger.info("Streamed turn persisted", extra={"turns": len(history), "tokens": len(tokens)})

    chat_turns = [turn for turn in history if turn["agent"] == "chat"]
    assert len(chat_turns) == 1, f"expected one persisted streamed turn, got {history}"
    # The persisted content is the reassembled stream, not a single chunk —
    # asserted against what this test actually received rather than any
    # expectation about the model's wording (spec-0029 R2.3).
    assert chat_turns[0]["response"]["content"] == "".join(tokens)
