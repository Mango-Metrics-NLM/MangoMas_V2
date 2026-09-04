"""Tier-1 flow I1: a streamed turn reaches ``GET /history`` (spec-0025).

Spec-0025's central claim is that a fully drained SSE stream persists its turn
— so a streamed conversation is visible in history, in the summarize agent's
window, and under tenancy scoping, exactly like an invoked one. Nothing tested
that end to end: every test in ``tests/test_streaming.py`` builds its
orchestrator with ``repo=None`` (``tests/test_streaming.py:26``), so the
persistence branch is never reached there, and the orchestrator-level tests
never cross the HTTP boundary.

Both directions, because "persist on full drain" is only half the contract:
a stream that fails mid-way must persist **nothing** (ADR-0025 — a
half-drained stream is not a turn).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mangomas.errors import LLMUnavailable
from tests.constants import STUB_REPLY
from tests.fakes import FakeLLM
from tests.integration.conftest import (
    ComposedApp,
    ComposeFn,
    read_history,
    turn_content,
    turn_metadata,
)

pytestmark = pytest.mark.integration

_STREAM_ROUTE = "/agents/chat/stream"
_MESSAGE_BODY = {"messages": [{"role": "user", "content": "stream please"}]}
# Enough chunks that "failed after the first" is distinguishable from
# "failed before any" — the truncation contract turns on that difference.
_CHUNKS = ["alpha ", "beta ", "gamma"]


async def _drain(composed: ComposedApp) -> list[dict[str, Any]]:
    """POST the stream route and return every parsed SSE frame."""
    frames: list[dict[str, Any]] = []
    async with (
        composed.client() as client,
        client.stream("POST", _STREAM_ROUTE, json=_MESSAGE_BODY) as response,
    ):
        assert response.status_code == 200, response.reason_phrase
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                frames.append(json.loads(line[len("data: ") :]))
    return frames


async def test_fully_drained_stream_persists_one_turn(compose_app: ComposeFn) -> None:
    """The whole point of spec-0025, asserted where a user would see it.

    Mutation proof: deleting the ``save_turn`` call in
    ``Orchestrator._stream_agent`` leaves every existing streaming test green
    and fails this one.
    """
    composed = compose_app(llm=FakeLLM(chunks=_CHUNKS))

    frames = await _drain(composed)

    assert [f for f in frames if f.get("event") == "token"], "expected token frames"
    assert frames[-1] == {"event": "done"}

    turns = await read_history(composed)
    assert len(turns) == 1, f"expected exactly one persisted turn, got {turns}"
    assert turns[0]["agent"] == "chat"
    # The persisted content is the reassembled stream, not one chunk.
    assert turn_content(turns[0]) == "".join(_CHUNKS)
    # And it carries the stream telemetry spec-0025 added, so a persisted
    # streamed turn is distinguishable from an invoked one after the fact.
    assert turn_metadata(turns[0])["stream"] == {"chunks": len(_CHUNKS), "degraded": False}


async def test_mid_stream_failure_persists_nothing(compose_app: ComposeFn) -> None:
    """A half-drained stream is not a turn (ADR-0025).

    The failure cannot become an error envelope: the 200 and the
    ``text/event-stream`` headers are already on the wire when the LLM raises.
    Over in-process ASGI transport Starlette then re-raises it as a generic
    ``RuntimeError`` about the response already having started — so **both the
    exception type and its message are Starlette-version artefacts**, and
    asserting on either would test Starlette rather than this contract. This
    asserts only that the drain did not complete normally, and then asserts
    the thing that actually matters: nothing reached storage.

    **Why no assertion on the exception.** The type *and* the message are both
    Starlette-version artefacts: the 200 and the ``text/event-stream`` headers
    are already on the wire when the LLM raises, so Starlette re-raises it as a
    generic "response already started" ``RuntimeError``. Matching either would
    test Starlette. Only that the drain did not complete normally is asserted.

    **Non-vacuity, and its limit.** ``llm.calls`` proves the request reached
    the LLM, ruling out the empty-history-because-nothing-happened reading (a
    404, a misrouted request). It does *not* detect a deliberately sabotaged
    precondition inside ``_drain`` — two better-looking guards were tried and
    both failed against that: filtering ``AssertionError`` does not work
    because the transport replaces the exception during teardown, and
    asserting on frames received before the failure does not work because
    ``httpx.ASGITransport`` delivers none of them (the same non-incremental
    behaviour that keeps stream *abandonment* out of this tier entirely). The
    mutation that matters — the stream no longer failing — is caught by
    ``drain_failed``.

    Truncation itself is already pinned at unit level by
    ``tests/test_streaming.py::test_mid_stream_failure_truncates_without_done_or_error_frame``;
    the persistence half has no other home.
    """
    composed = compose_app(
        llm=FakeLLM(
            chunks=_CHUNKS,
            raise_on_stream=LLMUnavailable("upstream gone"),
            raise_after_chunks=1,
        )
    )

    drain_failed = False
    try:
        await _drain(composed)
    except Exception:
        drain_failed = True

    assert drain_failed, "a mid-stream LLM failure must break the drain, not complete it"
    assert composed.llm.calls, (
        "the request never reached the LLM, so an empty history proves nothing"
    )
    assert await read_history(composed) == [], (
        "a stream that failed mid-drain must persist no turn (ADR-0025)"
    )


async def test_invoke_and_stream_persist_symmetrically(compose_app: ComposeFn) -> None:
    """Streamed and invoked turns are the same kind of thing in history.

    Spec-0025's framing is *parity*: before it, a streamed conversation was
    invisible while an identical invoked one was not. Asserting the two
    together is what pins the parity rather than each half separately.
    """
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY, chunks=[STUB_REPLY]))

    async with composed.client() as client:
        invoked = await client.post("/agents/chat/invoke", json=_MESSAGE_BODY)
    assert invoked.status_code == 200, invoked.text
    await _drain(composed)

    turns = await read_history(composed)
    assert len(turns) == 2
    assert {turn["agent"] for turn in turns} == {"chat"}
    assert all(turn_content(turn) == STUB_REPLY for turn in turns)
