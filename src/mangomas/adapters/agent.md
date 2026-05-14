# Adapters — `src/mangomas/adapters/`

Infrastructure adapters that satisfy protocol types defined in `base.py` files.
Concrete implementations are **never imported outside `composition.py`**.

## Structure

```
adapters/
├── llm/
│   ├── base.py       LLMClient + StreamingLLMClient protocols
│   └── lmstudio.py   LMStudioAdapter (OpenAI-compatible)
└── storage/
    ├── base.py       TurnRepository + MemoryRepository protocols
    ├── sqlite.py     SQLiteRepository (TurnRepository impl)
    └── memory.py     FileMemoryRepository (MemoryRepository impl)
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
2. Implement in a new file (e.g. `adapters/storage/redis.py`).
3. Export from `adapters/<layer>/__init__.py`.
4. Register a factory in `composition.py` using the appropriate registry.
5. Write `tests/test_<adapter>.py` using `FakeRepository` or similar fakes.
