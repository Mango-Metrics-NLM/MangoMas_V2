"""In-memory test doubles satisfying LLMClient and TurnRepository protocols."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.core.tools import ToolSpec
from tests.constants import (
    DEFAULT_TOOL_NAME,
    DEFAULT_TOOL_RESULT,
    STUB_REPLY,
    STUB_VERTEX_REPLY,
)


@dataclass
class FakeLLM:
    """In-memory stub satisfying the :class:`~mangomas.adapters.llm.base.LLMClient` protocol.

    Also satisfies ``PingableLLMClient`` and ``StreamingLLMClient`` extension protocols.
    """

    reply: str = STUB_REPLY
    replies: list[str] = field(default_factory=list)
    calls: list[list[Message]] = field(default_factory=list)
    closed: bool = False
    pinged: bool = False
    ping_error: BaseException | None = None
    # Token chunks for streaming; defaults to [reply] when empty.
    chunks: list[str] = field(default_factory=list)

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,  # noqa: ARG002
    ) -> str:
        self.calls.append(list(messages))
        idx = len(self.calls) - 1
        if self.replies and idx < len(self.replies):
            return self.replies[idx]
        return self.reply

    async def ping(self) -> None:
        self.pinged = True
        if self.ping_error is not None:
            raise self.ping_error

    async def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,  # noqa: ARG002
    ) -> AsyncIterator[str]:
        return self._fake_stream(messages)

    async def _fake_stream(self, messages: list[Message]) -> AsyncGenerator[str, None]:
        self.calls.append(list(messages))
        for chunk in self.chunks if self.chunks else [self.reply]:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class NonPingableFakeLLM:
    """Minimal LLM stub that does NOT expose ``ping()`` — tests the 'unknown' readiness path."""

    reply: str = STUB_REPLY
    calls: list[list[Message]] = field(default_factory=list)
    closed: bool = False

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,  # noqa: ARG002
    ) -> str:
        self.calls.append(list(messages))
        return self.reply

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class FakeRepository:
    """In-memory stub satisfying the TurnRepository protocol."""

    _turns: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    async def save_turn(
        self,
        agent: str,
        request: AgentRequest,
        response: AgentResponse,
    ) -> int:
        row_id = len(self._turns) + 1
        self._turns.append(
            {
                "id": row_id,
                "ts": "2026-05-13T00:00:00+00:00",
                "agent": agent,
                "request": request.model_dump(),
                "response": response.model_dump(),
            }
        )
        return row_id

    async def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._turns[-limit:]))

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeTool:
    """In-memory stub satisfying the :class:`~mangomas.core.tools.Tool` protocol."""

    name: str = DEFAULT_TOOL_NAME
    result: str = DEFAULT_TOOL_RESULT
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=f"Fake tool: {self.name}")

    async def execute(self, arguments: dict[str, Any]) -> str:
        self.calls.append(dict(arguments))
        return self.result


@dataclass
class _FakeGenerationResponse:
    """Mimics ``vertexai.generative_models.GenerationResponse`` for tests."""

    text: str


@dataclass
class FakeVertexGenerativeModel:
    """Test double for ``vertexai.generative_models.GenerativeModel``.

    Injected into :class:`mangomas.adapters.llm.vertex.VertexClient` via its
    ``client`` constructor argument so unit tests can drive the adapter without
    importing the real Vertex SDK.

    Behaviour:
        * ``reply`` / ``replies`` mirror :class:`FakeLLM` semantics.
        * ``chunks`` drives streaming responses; defaults to ``[reply]``.
        * Setting ``raise_on_call`` causes the *next* call (regardless of
          method) to raise the given exception — used to exercise the
          ``_translate_vertex_error`` matrix.
    """

    reply: str = STUB_VERTEX_REPLY
    replies: list[str] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    raise_on_call: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    async def generate_content_async(
        self,
        contents: Any,
        *,
        generation_config: dict[str, Any] | None = None,
        stream: bool = False,
        **_: Any,
    ) -> Any:
        self.calls.append(
            {
                "contents": contents,
                "generation_config": generation_config,
                "stream": stream,
            }
        )
        if self.raise_on_call is not None:
            exc = self.raise_on_call
            self.raise_on_call = None
            raise exc
        if stream:
            return self._stream_chunks()
        idx = len(self.calls) - 1
        text = self.replies[idx] if self.replies and idx < len(self.replies) else self.reply
        return _FakeGenerationResponse(text=text)

    async def _stream_chunks(self) -> AsyncGenerator[_FakeGenerationResponse, None]:
        chunks = self.chunks if self.chunks else [self.reply]
        for chunk in chunks:
            yield _FakeGenerationResponse(text=chunk)

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class FakeSecretsProvider:
    """In-memory stub satisfying :class:`mangomas.secrets.SecretsProvider`."""

    values: dict[str, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def get(self, name: str) -> str | None:
        self.calls.append(name)
        return self.values.get(name)


@dataclass
class FakeMemoryRepository:
    """In-memory stub satisfying
    :class:`~mangomas.adapters.storage.base.MemoryRepository`.
    """

    episodic_entries: list[str] = field(default_factory=list)
    index_content: str = ""
    closed: bool = False

    async def write_episodic(self, content: str, *, prefix: str = "") -> str:
        self.episodic_entries.append(content)
        return f"fake/{prefix or 'entry'}.md"

    async def read_index(self) -> str:
        return self.index_content

    async def append_index(self, entry: str) -> None:
        self.index_content = self.index_content + ("\n" if self.index_content else "") + entry

    def close(self) -> None:
        self.closed = True
