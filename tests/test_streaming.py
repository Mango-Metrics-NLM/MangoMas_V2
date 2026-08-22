"""Tests for the SSE streaming endpoint /agents/{name}/stream."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import MutableMapping
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from mangomas.adapters.llm.base import LLMClient
from mangomas.agents import ChatAgent
from mangomas.api.app import create_app
from mangomas.core import AgentContext, Orchestrator
from tests.constants import STUB_REPLY
from tests.fakes import FakeLLM, NonPingableFakeLLM

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_client(llm: LLMClient | None = None) -> TestClient:
    fake_llm = llm if llm is not None else FakeLLM()
    ctx = AgentContext(llm=fake_llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    app = create_app(orchestrator=orch)
    return TestClient(app)


def _parse_sse(text: str) -> list[dict[str, object]]:
    """Parse text/event-stream lines into a list of JSON data payloads."""
    payloads: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            raw = line[len("data: ") :]
            payload = json.loads(raw)
            assert isinstance(payload, dict)
            payloads.append(payload)
    return payloads


def _token_contents(payloads: list[dict[str, object]]) -> list[str]:
    contents: list[str] = []
    for payload in payloads:
        if payload.get("event") == "token":
            data = payload.get("data")
            assert isinstance(data, dict)
            content = data.get("content")
            assert isinstance(content, str)
            assert payload.get("content") == content
            contents.append(content)
    return contents


_INVOKE_BODY = {
    "messages": [{"role": "user", "content": "hi"}],
}


# ── Streaming tests ───────────────────────────────────────────────────────────


def test_stream_endpoint_yields_chunks() -> None:
    llm = FakeLLM(chunks=["Hello", " world", "!"])
    client = _make_client(llm=llm)
    r = client.post("/agents/chat/stream", json=_INVOKE_BODY)
    assert r.status_code == 200
    payloads = _parse_sse(r.text)
    assert _token_contents(payloads) == ["Hello", " world", "!"]


def test_stream_endpoint_done_event_present() -> None:
    client = _make_client()
    r = client.post("/agents/chat/stream", json=_INVOKE_BODY)
    assert r.status_code == 200
    payloads = _parse_sse(r.text)
    assert any(p.get("event") == "done" for p in payloads)


def test_stream_endpoint_unknown_agent_returns_404() -> None:
    client = _make_client()
    r = client.post("/agents/ghost/stream", json=_INVOKE_BODY)
    assert r.status_code == 404


def test_stream_content_type_is_event_stream() -> None:
    client = _make_client()
    r = client.post("/agents/chat/stream", json=_INVOKE_BODY)
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]


def test_stream_fallback_non_streaming_llm(caplog: pytest.LogCaptureFixture) -> None:
    """Agent without StreamingLLMClient falls back to handle() → single chunk."""
    llm = NonPingableFakeLLM(reply="fallback-reply")
    # Wire a non-streaming LLM client; ChatAgent will fall back to complete()
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    app = create_app(orchestrator=orch)
    with (
        caplog.at_level(logging.WARNING, logger="mangomas.agents.chat"),
        TestClient(app) as client,
    ):
        r = client.post("/agents/chat/stream", json=_INVOKE_BODY)
    assert r.status_code == 200
    payloads = _parse_sse(r.text)
    assert _token_contents(payloads) == ["fallback-reply"]
    assert "does not support streaming" in caplog.text


def test_stream_single_default_reply() -> None:
    """Default FakeLLM with no chunks yields the single reply as one chunk."""
    client = _make_client()
    r = client.post("/agents/chat/stream", json=_INVOKE_BODY)
    assert r.status_code == 200
    payloads = _parse_sse(r.text)
    assert _token_contents(payloads) == [STUB_REPLY]


async def test_mid_stream_failure_truncates_without_done_or_error_frame() -> None:
    """A mid-stream upstream failure truncates the SSE response (spec-0022 R14).

    The contract lives in the frame placement in api/routes/agents.py: the
    ``done`` frame sits *after* the ``aclosing`` block, so an exception from
    the upstream iterator skips it — the client sees the tokens that arrived,
    then the stream simply ends. No ``done`` frame (the client must not treat
    a truncated answer as complete) and no invented error frame (the 200 +
    text/event-stream headers are already sent, so the MangomasError JSON
    envelope cannot run; fabricating an SSE error event would be a new,
    undocumented frame kind). Asserted in prose in three places, tested
    nowhere until now.
    """
    llm = FakeLLM(
        chunks=["a", "b", "c"],
        raise_on_stream=RuntimeError("upstream died"),
        raise_after_chunks=2,
    )
    ctx = AgentContext(llm=llm, repo=None)
    orch = Orchestrator(ctx)
    orch.register(ChatAgent())
    app = create_app(orchestrator=orch)

    # Both TestClient and httpx.ASGITransport run the app to completion and
    # surface the pending exception INSTEAD of the frames that were already
    # sent — so the truncation contract is only observable at the raw ASGI
    # boundary: drive the app directly and record every `send` message that
    # happened before the raise.
    body = json.dumps(_INVOKE_BODY).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/agents/chat/stream",
        "raw_path": b"/agents/chat/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"host", b"testserver"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    request_messages: list[MutableMapping[str, Any]] = [
        {"type": "http.request", "body": body, "more_body": False}
    ]
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        if request_messages:
            return request_messages.pop(0)
        # Never deliver http.disconnect: StreamingResponse listens for it
        # concurrently and would cancel the stream before the mid-stream
        # raise this test exists to observe. Parking forever is safe — the
        # raise cancels this pending receive via the response's task group.
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    with pytest.raises(RuntimeError, match="upstream died"):
        await app(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    streamed = b"".join(
        bytes(cast("bytes", m.get("body", b""))) for m in sent if m["type"] == "http.response.body"
    )
    payloads = _parse_sse(streamed.decode())
    assert _token_contents(payloads) == ["a", "b"]
    assert {"event": "done"} not in payloads
    assert all(payload.get("event") == "token" for payload in payloads)
