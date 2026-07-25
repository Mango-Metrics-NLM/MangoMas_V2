# Adapters — `src/mangomas/adapters/`

Infrastructure adapters that satisfy protocol types defined in `base.py` files.
Concrete implementations are **never imported outside `composition.py`**.

## Structure

Underscore-prefixed modules at the package root are **shared helpers**, not
adapters: same-layer code the concrete adapters reuse so they cannot drift.

```
adapters/
├── _http_errors.py       translate_httpx_error — httpx → typed LLMError
├── _vertex_errors.py     translate_vertex_error — Vertex qualname matrix
├── _openai_client.py     OpenAICompatHTTPClient — shared httpx lifecycle base
├── llm/
│   ├── base.py           LLMClient / StreamingLLMClient / PingableLLMClient
│   ├── lmstudio.py       LMStudioClient (OpenAI-compatible)
│   └── vertex.py         VertexClient (lazy SDK import; `vertex` extra)
├── embeddings/
│   ├── base.py           EmbeddingClient protocol
│   ├── _shared.py        SingleTextEmbedMixin + NoTransportAcloseMixin
│   ├── lmstudio.py       LMStudioEmbeddingClient (httpx)
│   ├── sentence_transformers.py  in-process (`embeddings-local` extra)
│   └── vertex.py         VertexEmbeddingClient (ADC only; `vertex` extra)
├── vector/
│   ├── base.py           VectorStoreRepository protocol + VectorMatch
│   └── chroma.py         ChromaVectorStore (`rag` extra)
└── storage/
    ├── base.py           TurnRepository + MemoryRepository protocols
    ├── sqlite.py         SQLiteRepository (TurnRepository impl)
    ├── postgres.py       PostgresRepository (asyncpg; `postgres` extra)
    └── memory.py         FileMemoryRepository (MemoryRepository impl)
```

## Protocols

```python
# adapters/llm/base.py
class LLMClient(Protocol):
    async def complete(self, messages: list[Message]) -> str: ...

class StreamingLLMClient(LLMClient, Protocol):
    async def stream(self, messages: list[Message]) -> AsyncIterator[str]: ...

# adapters/storage/base.py
class TurnRepository(Protocol):
    async def save(self, turn: Turn) -> None: ...
    async def list_turns(self, limit: int = 20) -> list[Turn]: ...
    def close(self) -> None: ...

class MemoryRepository(Protocol):
    async def write_episodic(self, content: str, *, prefix: str = "") -> str: ...
    async def read_index(self) -> str: ...
    async def append_index(self, entry: str) -> None: ...
    def close(self) -> None: ...
```

## Rules

- **Satisfy the Protocol** — before writing an adapter, read its Protocol in `base.py`.
- **`asyncio.to_thread`** for any synchronous I/O (file reads, DB writes).
- **Register in `composition.py`** — not in the adapter module itself.
- **Settings-driven** — read connection strings, URLs, and paths from `Settings`, not from hardcoded values.
- OpenTelemetry spans for all network/IO operations.

## Adding a New Adapter

1. Define/extend the Protocol in `base.py` if needed.
2. **Reuse the shared helpers before writing new plumbing:**
   - An OpenAI-compatible HTTP provider subclasses `OpenAICompatHTTPClient`
     (`_openai_client.py`), sets the `_LABEL` / `_BAD_RESPONSE` ClassVars, and
     implements only its call method — the base owns base-URL normalisation,
     bearer-auth client construction, injected-vs-owned client tracking,
     `_translate_error()`, and `aclose()`.
   - Any other httpx adapter calls `translate_httpx_error` directly; a Vertex
     adapter calls `translate_vertex_error`. Never hand-roll
     `except TimeoutError: raise LLMTimeout(...)`.
   - An embedding backend mixes in `SingleTextEmbedMixin` (and
     `NoTransportAcloseMixin` if it owns no sockets) and implements only
     `embed_batch`.
3. Implement in a new file (e.g. `adapters/storage/redis.py`).
4. Export from `adapters/<layer>/__init__.py`.
5. Register a factory in `composition.py` using the appropriate registry.
6. Write `tests/adapters/test_<adapter>.py` using `FakeRepository` or similar
   fakes; lock any shared-helper contract in `tests/adapters/`.
