"""Unit tests for :class:`VertexLLMClient`.

Uses constructor-injected fake ``GenerativeModel`` instances and
stand-in google.api_core / google.auth exception modules — no real
Google SDK is required at test time.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from mangomas.adapters.llm.base import LLMClient, PingableLLMClient, StreamingLLMClient
from mangomas.adapters.llm.vertex import (
    VertexError,
    VertexLLMClient,
    _to_vertex_messages,
)
from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable

# ── Synthetic Google exception modules (same trick as test_secrets_gcp) ───────


class _DeadlineExceeded(Exception):
    pass


class _RetryError(Exception):
    pass


class _ServiceUnavailable(Exception):
    pass


class _Aborted(Exception):
    pass


class _GoogleAPIError(Exception):
    pass


class _OtherAPIError(_GoogleAPIError):
    pass


class _DefaultCredentialsError(Exception):
    pass


@pytest.fixture(autouse=True)
def _stub_google_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    gax = types.ModuleType("google.api_core.exceptions")
    gax.DeadlineExceeded = _DeadlineExceeded  # type: ignore[attr-defined]
    gax.RetryError = _RetryError  # type: ignore[attr-defined]
    gax.ServiceUnavailable = _ServiceUnavailable  # type: ignore[attr-defined]
    gax.Aborted = _Aborted  # type: ignore[attr-defined]
    gax.GoogleAPIError = _GoogleAPIError  # type: ignore[attr-defined]

    gauth_exc = types.ModuleType("google.auth.exceptions")
    gauth_exc.DefaultCredentialsError = _DefaultCredentialsError  # type: ignore[attr-defined]

    for pkg in ("google", "google.api_core", "google.auth"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", gax)
    monkeypatch.setitem(sys.modules, "google.auth.exceptions", gauth_exc)


# ── Fake Vertex GenerativeModel ───────────────────────────────────────────────


@dataclass
class _FakePart:
    text: str


@dataclass
class _FakeContent:
    parts: list[_FakePart]


@dataclass
class _FakeCandidate:
    content: _FakeContent


@dataclass
class _FakeResponse:
    """Mimics the Vertex SDK ``GenerateContentResponse`` shape."""

    text: str = ""
    candidates: list[_FakeCandidate] = field(default_factory=list)


def _response(text: str) -> _FakeResponse:
    return _FakeResponse(
        text=text,
        candidates=[_FakeCandidate(content=_FakeContent(parts=[_FakePart(text=text)]))],
    )


def _empty_response() -> _FakeResponse:
    return _FakeResponse(text="", candidates=[])


@dataclass
class _FakeModel:
    """Constructor-injected stand-in for vertexai.GenerativeModel."""

    response: _FakeResponse | None = None
    stream_chunks: list[_FakeResponse] = field(default_factory=list)
    raises: BaseException | None = None
    stream_raises_mid: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate_content(
        self,
        contents: list[dict[str, Any]],
        *,
        generation_config: dict[str, Any] | None = None,
        system_instruction: str | None = None,
        stream: bool = False,
    ) -> Any:
        self.calls.append(
            {
                "contents": contents,
                "generation_config": generation_config,
                "system_instruction": system_instruction,
                "stream": stream,
            }
        )
        if self.raises is not None:
            raise self.raises
        if stream:
            return _FakeStreamIterator(
                chunks=list(self.stream_chunks),
                raise_mid=self.stream_raises_mid,
            )
        return self.response or _empty_response()


@dataclass
class _FakeStreamIterator:
    chunks: list[_FakeResponse]
    raise_mid: BaseException | None = None
    _emitted: int = 0

    def __iter__(self) -> _FakeStreamIterator:
        return self

    def __next__(self) -> _FakeResponse:
        if self._emitted >= len(self.chunks):
            raise StopIteration
        if self.raise_mid is not None and self._emitted == 1:
            raise self.raise_mid
        chunk = self.chunks[self._emitted]
        self._emitted += 1
        return chunk


# ── Helpers ───────────────────────────────────────────────────────────────────


def _client(model: _FakeModel) -> VertexLLMClient:
    return VertexLLMClient(
        project="proj",
        location="us-central1",
        model="gemini-1.5-flash",
        request_timeout_seconds=60.0,
        default_temperature=0.2,
        max_output_tokens=None,
        client=model,
    )


# ── Protocol parity ──────────────────────────────────────────────────────────


def test_satisfies_all_protocols() -> None:
    c = _client(_FakeModel(response=_response("hi")))
    assert isinstance(c, LLMClient)
    assert isinstance(c, StreamingLLMClient)
    assert isinstance(c, PingableLLMClient)


# ── _to_vertex_messages ───────────────────────────────────────────────────────


def test_to_vertex_collapses_system_messages() -> None:
    msgs = [
        Message(role="system", content="be brief"),
        Message(role="system", content="be polite"),
        Message(role="user", content="hello"),
    ]
    sys_instruction, contents = _to_vertex_messages(msgs)
    assert sys_instruction == "be brief\n\nbe polite"
    assert contents == [{"role": "user", "parts": [{"text": "hello"}]}]


def test_to_vertex_maps_assistant_to_model_role() -> None:
    msgs = [
        Message(role="user", content="q"),
        Message(role="assistant", content="a"),
    ]
    _, contents = _to_vertex_messages(msgs)
    assert contents == [
        {"role": "user", "parts": [{"text": "q"}]},
        {"role": "model", "parts": [{"text": "a"}]},
    ]


def test_to_vertex_rejects_tool_role() -> None:
    with pytest.raises(LLMBadResponse, match="tool-role"):
        _to_vertex_messages([Message(role="tool", content="...")])


# ── complete() ────────────────────────────────────────────────────────────────


async def test_complete_returns_text() -> None:
    model = _FakeModel(response=_response("the answer"))
    c = _client(model)

    out = await c.complete([Message(role="user", content="q")])
    assert out == "the answer"
    assert len(model.calls) == 1
    call = model.calls[0]
    assert call["generation_config"]["temperature"] == 0.2  # default
    assert call["stream"] is False


async def test_complete_raises_vertex_error_on_empty_response() -> None:
    c = _client(_FakeModel(response=_empty_response()))
    with pytest.raises(VertexError, match="no candidate text"):
        await c.complete([Message(role="user", content="q")])


async def test_complete_translates_deadline_to_llm_timeout() -> None:
    c = _client(_FakeModel(raises=_DeadlineExceeded("slow")))
    with pytest.raises(LLMTimeout, match="timed out"):
        await c.complete([Message(role="user", content="q")])


async def test_complete_translates_service_unavailable() -> None:
    c = _client(_FakeModel(raises=_ServiceUnavailable("down")))
    with pytest.raises(LLMUnavailable, match="unavailable"):
        await c.complete([Message(role="user", content="q")])


async def test_complete_translates_missing_adc() -> None:
    c = _client(_FakeModel(raises=_DefaultCredentialsError("no ADC")))
    with pytest.raises(LLMUnavailable, match="ADC not configured"):
        await c.complete([Message(role="user", content="q")])


async def test_complete_translates_other_google_api_error() -> None:
    c = _client(_FakeModel(raises=_OtherAPIError("boom")))
    with pytest.raises(VertexError, match="returned an error"):
        await c.complete([Message(role="user", content="q")])


async def test_complete_translates_unknown_exception_to_unavailable() -> None:
    c = _client(_FakeModel(raises=RuntimeError("network borked")))
    with pytest.raises(LLMUnavailable, match="unreachable"):
        await c.complete([Message(role="user", content="q")])


async def test_complete_respects_custom_temperature() -> None:
    model = _FakeModel(response=_response("x"))
    c = _client(model)
    await c.complete([Message(role="user", content="q")], temperature=0.7)
    assert model.calls[0]["generation_config"]["temperature"] == 0.7


# ── stream() ──────────────────────────────────────────────────────────────────


async def test_stream_yields_concatenated_tokens() -> None:
    model = _FakeModel(stream_chunks=[_response("Hello"), _response(" "), _response("world")])
    c = _client(model)
    tokens: list[str] = []
    async for tok in await c.stream([Message(role="user", content="q")]):
        tokens.append(tok)
    assert "".join(tokens) == "Hello world"
    assert model.calls[0]["stream"] is True


async def test_stream_mid_stream_unavailable_translates() -> None:
    model = _FakeModel(
        stream_chunks=[_response("first"), _response("second")],
        stream_raises_mid=_ServiceUnavailable("dropped"),
    )
    c = _client(model)
    tokens: list[str] = []
    with pytest.raises(LLMUnavailable):
        async for tok in await c.stream([Message(role="user", content="q")]):
            tokens.append(tok)
    assert tokens == ["first"]


async def test_stream_start_error_translates() -> None:
    c = _client(_FakeModel(raises=_DeadlineExceeded("slow")))
    iterator = await c.stream([Message(role="user", content="q")])
    with pytest.raises(LLMTimeout):
        async for _ in iterator:
            pass


# ── ping() ────────────────────────────────────────────────────────────────────


async def test_ping_succeeds() -> None:
    model = _FakeModel(response=_response("pong"))
    c = _client(model)
    await c.ping()
    assert len(model.calls) == 1
    assert model.calls[0]["generation_config"]["max_output_tokens"] == 1


async def test_ping_translates_unavailable() -> None:
    c = _client(_FakeModel(raises=_ServiceUnavailable("down")))
    with pytest.raises(LLMUnavailable):
        await c.ping()


# ── aclose() ──────────────────────────────────────────────────────────────────


async def test_aclose_is_noop() -> None:
    c = _client(_FakeModel(response=_response("x")))
    await c.aclose()  # no exception, no return value
