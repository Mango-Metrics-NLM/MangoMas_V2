"""Tests for the SSE streaming endpoint /agents/{name}/stream."""

from __future__ import annotations

import json
import logging

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
