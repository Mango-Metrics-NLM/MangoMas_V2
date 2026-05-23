---
name: mango-adapter
description: >
  Implementing a new adapter (LLM or storage) in Mango-Mas V2. Use when:
  adding a new LLM provider (Vertex AI, Anthropic, etc.), adding a new
  storage backend (Postgres, Cloud SQL), implementing the SecretsProvider
  protocol, or extending an existing adapter while preserving the
  Protocol-first contract. Covers the registry registration step in
  composition.py, the @runtime_checkable Protocol surface, and the fake
  adapter pattern in tests/fakes.py.
argument-hint: "Describe the adapter to add (e.g. 'vertex LLM provider') or paste a failing protocol-conformance test"
---

# Mango-Mas Adapter Skill

## When to Use

- Add a new `LLMClient` (e.g. Vertex AI, Anthropic, OpenAI) under `src/mangomas/adapters/llm/`
- Add a new `TurnRepository` (e.g. Postgres) under `src/mangomas/adapters/storage/`
- Add a new `MemoryRepository` provider
- Add a new `SecretsProvider` (e.g. GCP Secret Manager) under `src/mangomas/secrets/`
- Extend an existing adapter to satisfy an additional optional protocol
  (`PingableLLMClient`, `StreamingLLMClient`)
- Diagnose a `UnknownProvider` error at composition time

---

## Quick Commands

```powershell
# Verify the adapter satisfies its Protocol at import time
python -c "from mangomas.adapters.llm.base import LLMClient; from mangomas.adapters.llm.lmstudio import LMStudioClient; assert isinstance(LMStudioClient.__new__(LMStudioClient), LLMClient)"

# Run only the adapter tests
python -m pytest tests/test_lmstudio.py tests/test_sqlite.py -v

# Full suite (must stay green)
python -m pytest --tb=short -q
```

```bash
# Same commands, bash flavour
python -m pytest tests/test_lmstudio.py tests/test_sqlite.py -v
python -m pytest --tb=short -q
```

---

## Adapter Rules

| Rule | Detail |
|------|--------|
| Protocol-first | Implement against `@runtime_checkable Protocol` in `adapters/*/base.py`. Never import a concrete adapter from `core/` or `agents/`. |
| Composition-root registration | Register the factory in `composition.py` via `llm_registry.register("name", factory)` / `_storage_registry.register(...)` / `_memory_registry.register(...)`. No other module registers adapters. |
| Config-driven | New tunables go in `LLMSettings`, `DBSettings`, `MemorySettings`, or `SecretsSettings` in `config.py`. Never hard-code URLs, model IDs, or timeouts. |
| Secrets seam | API keys / credentials resolve through `_resolve_llm_secrets()` in `composition.py`; do NOT call `os.environ` directly inside the adapter. |
| Errors typed | Surface failures as `LLMTimeout`, `LLMUnavailable`, `LLMBadResponse`, `LLMError`, or `PersistenceError` — never bare `Exception`. |
| Async I/O | All public methods are `async def`. Synchronous I/O wrapped in `asyncio.to_thread(...)`. |
| Telemetry | Open a span via `get_tracer(__name__).start_as_current_span("adapter.<provider>.<method>")`; emit structured logs via `logger.info(msg, extra={...})`. |
| Streaming | If the upstream supports it, also satisfy `StreamingLLMClient`. If not, leave `.stream` unimplemented — `agents/_streaming.py` handles the buffered fallback. |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/adapters/llm/base.py` | `LLMClient`, `PingableLLMClient`, `StreamingLLMClient` Protocols |
| `src/mangomas/adapters/llm/lmstudio.py` | Reference implementation with httpx + retries |
| `src/mangomas/adapters/storage/base.py` | `TurnRepository`, `MemoryRepository` Protocols |
| `src/mangomas/adapters/storage/sqlite.py` | Reference SQLite-backed `TurnRepository` |
| `src/mangomas/adapters/storage/memory.py` | Reference file-backed `MemoryRepository` |
| `src/mangomas/secrets/provider.py` | `SecretsProvider` Protocol |
| `src/mangomas/secrets/env.py` | `EnvSecretsProvider` reference |
| `src/mangomas/composition.py` | Single wiring point — `llm_registry`, `_storage_registry`, `_memory_registry`, `secrets_registry` |
| `src/mangomas/registry.py` | Generic `Registry[T]` with `scoped()` ctx manager for test isolation |
| `tests/fakes.py` | `FakeLLM`, `FakeRepository`, `FakeMemoryRepository`, `FakeSecretsProvider` — extend instead of using `mock.patch` |

---

## Template — New LLM Adapter

```python
# src/mangomas/adapters/llm/<provider>.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

from mangomas.adapters.llm.base import LLMClient
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable
from mangomas.telemetry import get_tracer

if TYPE_CHECKING:
    from mangomas.config import LLMSettings
    from mangomas.core.agent import Message

logger = logging.getLogger(__name__)
_tracer = get_tracer(__name__)


class <Provider>Client:
    """LLMClient backed by <provider>."""

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        # construct the SDK client here, no I/O

    async def complete(
        self, messages: list[Message], *, temperature: float | None = None
    ) -> str:
        with _tracer.start_as_current_span("adapter.<provider>.complete") as span:
            span.set_attribute("provider", self._settings.provider)
            span.set_attribute("model", self._settings.model)
            try:
                return await self._call(messages, temperature)
            except TimeoutError as exc:
                raise LLMTimeout("timed out") from exc

    async def aclose(self) -> None:
        # release SDK resources
        ...
```

Then register in `composition.py`:

```python
from mangomas.adapters.llm.<provider> import <Provider>Client

llm_registry.register("<provider>", lambda settings: <Provider>Client(settings))
```

Activate via `MANGOMAS_LLM__PROVIDER=<provider>`.

---

## Workflow

1. Read the relevant Protocol in `adapters/*/base.py`.
2. Implement the adapter class in a new file under the matching adapter directory.
3. Add any new tunables to `config.py` (`LLMSettings`/`DBSettings`/etc.) with `DEFAULT_*` constants.
4. Register the factory in `composition.py` only.
5. Write `tests/test_<adapter>.py` — assert `isinstance(instance, Protocol)` and exercise success + each error path.
6. If the adapter needs a test double, extend `tests/fakes.py` (don't duplicate inline).
7. Run `ruff check --fix src tests` + `mypy --strict` + `pytest`.
8. Append a CHANGELOG entry under the unreleased section.

---

## Constraints

- DO NOT import a concrete adapter outside `composition.py`.
- DO NOT raise bare `Exception` — use `LLMError`/`PersistenceError` subclasses.
- DO NOT call `os.environ` in the adapter — use the `SecretsProvider` seam.
- DO NOT hardcode URLs, timeouts, model names — they belong in `Settings`.
- DO NOT add a `mock.patch` on the Protocol — extend `tests/fakes.py`.

---

## Diagnosing Failures

1. `UnknownProvider` at startup → the factory is not registered in `composition.py`.
2. `isinstance(client, LLMClient)` returns `False` → a Protocol method is missing or has the wrong signature; cross-check `adapters/llm/base.py`.
3. mypy errors about `Awaitable[str]` → an async method is missing `await` or returning the coroutine itself.
4. `LLMBadResponse` in CI but not locally → upstream JSON schema drift; pin the SDK and add a regression test.
