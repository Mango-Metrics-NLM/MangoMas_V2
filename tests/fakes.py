"""In-memory test doubles satisfying LLMClient and TurnRepository protocols."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mangomas.adapters.vector.base import VectorMatch
from mangomas.core.agent import AgentRequest, AgentResponse, Message
from mangomas.core.tools import ToolSpec
from tests.constants import (
    DEFAULT_TOOL_NAME,
    DEFAULT_TOOL_RESULT,
    FAKE_SINK_NAME,
    STUB_REPLY,
    STUB_VERTEX_REPLY,
)

if TYPE_CHECKING:
    from mangomas.core import AcceptanceFn
    from mangomas.eval import EvalReport, GateResult


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
    # Records the (temperature, max_tokens) kwargs each complete()/stream()
    # call received, index-aligned with `calls` (spec-0014 M5: proves the two
    # AgentSettings fields actually flow from agents through to the client).
    call_kwargs: list[dict[str, float | int | None]] = field(default_factory=list)

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(list(messages))
        self.call_kwargs.append({"temperature": temperature, "max_tokens": max_tokens})
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
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        return self._fake_stream(messages, temperature=temperature, max_tokens=max_tokens)

    async def _fake_stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        self.calls.append(list(messages))
        self.call_kwargs.append({"temperature": temperature, "max_tokens": max_tokens})
        for chunk in self.chunks if self.chunks else [self.reply]:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class NonPingableFakeLLM:
    """Minimal LLM stub that does NOT expose ``ping()`` — tests the 'unknown' readiness path.

    Deliberately has no ``stream()`` method either — this is what forces
    callers onto the ``stream_with_buffered_fallback`` buffered-``complete()``
    path, so ``complete()`` must accept the same kwargs that path forwards.
    """

    reply: str = STUB_REPLY
    calls: list[list[Message]] = field(default_factory=list)
    closed: bool = False

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,  # noqa: ARG002
        max_tokens: int | None = None,  # noqa: ARG002
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
class FakeEmbeddingClient:
    """In-memory stub satisfying the
    :class:`~mangomas.adapters.embeddings.base.EmbeddingClient` protocol.

    The deterministic embedding for a text is its character ordinals (padded to
    nothing — variable length), which is finite and reproducible. Tests that need
    fixed-dimension vectors should pass ``vectors`` keyed by text instead.
    """

    vectors: dict[str, list[float]] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    closed: bool = False

    async def embed(self, text: str) -> list[float]:
        result = await self.embed_batch([text])
        return result[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector_for(t) for t in texts]

    def _vector_for(self, text: str) -> list[float]:
        if text in self.vectors:
            return self.vectors[text]
        return [float(ord(c)) for c in text] or [0.0]

    async def aclose(self) -> None:
        self.closed = True


def _fake_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for the FakeVectorStore; 0.0 for empty/length-mismatch."""
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(dot / (na * nb))


@dataclass
class FakeVectorStore:
    """In-memory stub satisfying
    :class:`~mangomas.adapters.vector.base.VectorStoreRepository`.

    Stores records in a dict keyed by id and brute-forces cosine similarity on
    :meth:`query`, normalising ``cos ∈ [-1, 1]`` to a ``[0, 1]`` score via
    ``(cos + 1) / 2`` — the same convention the real Chroma adapter targets.
    """

    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    upserts: list[list[str]] = field(default_factory=list)
    deleted_sources: list[str] = field(default_factory=list)
    closed: bool = False

    async def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.upserts.append(list(ids))
        for i, doc_id in enumerate(ids):
            self.records[doc_id] = {
                "embedding": list(embeddings[i]),
                "document": documents[i],
                "metadata": dict(metadatas[i]),
            }

    async def query(self, *, embedding: list[float], top_k: int) -> list[VectorMatch]:
        scored = [
            VectorMatch(
                id=doc_id,
                document=rec["document"],
                score=(_fake_cosine(embedding, rec["embedding"]) + 1.0) / 2.0,
                metadata=dict(rec["metadata"]),
            )
            for doc_id, rec in self.records.items()
        ]
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:top_k]

    async def delete_by_source(self, source: str) -> int:
        self.deleted_sources.append(source)
        before = len(self.records)
        self.records = {
            doc_id: rec
            for doc_id, rec in self.records.items()
            if rec["metadata"].get("source") != source
        }
        return before - len(self.records)

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


@dataclass
class FakeSink:
    """In-memory stub satisfying :class:`mangomas.eval.Sink`.

    Records every ``(report, gate_result)`` pair it receives. Set
    ``raise_on_emit`` to exercise the CLI's per-sink fault isolation.
    """

    name: str = FAKE_SINK_NAME
    emitted: list[tuple[EvalReport, GateResult | None]] = field(default_factory=list)
    raise_on_emit: BaseException | None = None

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        if self.raise_on_emit is not None:
            raise self.raise_on_emit
        self.emitted.append((report, gate_result))


@dataclass
class FakeOrchestrator:
    """Minimal stand-in for :class:`mangomas.core.Orchestrator` in entry-point tests.

    Covers only the dispatch surface the workflow node executors drive
    (``dispatch`` + ``dispatch_fan_out``) plus the ``aclose`` teardown
    contract. Set ``raise_on_dispatch`` to make every dispatch fail — used to
    prove entry points still release adapter resources on arbitrary errors.
    """

    reply: str = STUB_REPLY
    raise_on_dispatch: BaseException | None = None
    dispatched: list[str] = field(default_factory=list)
    closed: bool = False

    async def dispatch(
        self,
        agent_name: str,
        request: AgentRequest,  # noqa: ARG002
        *,
        acceptance_fn: AcceptanceFn | None = None,  # noqa: ARG002
        max_steps: int | None = None,  # noqa: ARG002
    ) -> AgentResponse:
        self.dispatched.append(agent_name)
        if self.raise_on_dispatch is not None:
            raise self.raise_on_dispatch
        return AgentResponse(content=self.reply, agent=agent_name)

    async def dispatch_fan_out(
        self, agent_names: list[str], request: AgentRequest
    ) -> list[AgentResponse]:
        return [await self.dispatch(name, request) for name in agent_names]

    async def aclose(self) -> None:
        self.closed = True
