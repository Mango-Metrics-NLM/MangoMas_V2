---
name: mango-adapter
description: >
  Implementing a new adapter (LLM or storage) in Mango-Mas V2. Use when:
  adding a new LLM provider (Vertex AI, Anthropic, etc.), adding a new
  storage backend (Postgres, Cloud SQL), implementing the SecretsProvider
  protocol, or extending an existing adapter while preserving the
  Protocol-first contract. Covers the registry registration step in
  the composition/ package, the @runtime_checkable Protocol surface, and the fake
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
| Composition-root registration | Register the factory in the `composition/` package via `llm_registry.register("name", factory)` / `_storage_registry.register(...)` / `_memory_registry.register(...)`. No other module registers adapters. |
| Config-driven | New tunables go in `LLMSettings`, `DBSettings`, `MemorySettings`, or `SecretsSettings` in `mangomas.config`. Never hard-code URLs, model IDs, or timeouts. |
| Secrets seam | API keys / credentials resolve through `_resolve_llm_secrets()` in `composition/secrets.py`; do NOT call `os.environ` directly inside the adapter. |
| Errors typed | Surface failures as `LLMTimeout`, `LLMUnavailable`, `LLMBadResponse`, `LLMError`, or `PersistenceError` — never bare `Exception`. Do not hand-roll the mapping: HTTP backends call `_http_errors.translate_httpx_error`, Vertex backends call `_vertex_errors.translate_vertex_error`. |
| Reuse the shared base | An OpenAI-compatible HTTP upstream subclasses `adapters/_openai_client.py::OpenAICompatHTTPClient` rather than re-implementing base-URL normalisation, bearer-auth client construction, injected-vs-owned client tracking, and `aclose()`. |
| Async I/O | All public methods are `async def`. Synchronous I/O wrapped in `asyncio.to_thread(...)`. |
| Telemetry | Open a span via `get_tracer(__name__).start_as_current_span("adapter.<provider>.<method>")`; emit structured logs via `logger.info(msg, extra={...})`. |
| Streaming | If the upstream supports it, also satisfy `StreamingLLMClient`. If not, leave `.stream` unimplemented — `agents/_streaming.py` handles the buffered fallback. |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/adapters/llm/base.py` | `LLMClient`, `PingableLLMClient`, `StreamingLLMClient` Protocols |
| `src/mangomas/adapters/_openai_client.py` | `OpenAICompatHTTPClient` — shared httpx lifecycle for OpenAI-compatible upstreams (base-URL rstrip, bearer-auth client, owned-vs-injected tracking, `_translate_error`, `aclose`) |
| `src/mangomas/adapters/_http_errors.py` | `translate_httpx_error(exc, *, base_url, label, bad_response)` — the single httpx → typed-error mapping |
| `src/mangomas/adapters/_vertex_errors.py` | `translate_vertex_error(...)` — the Vertex qualname error matrix (llm + embeddings) |
| `src/mangomas/adapters/llm/lmstudio.py` | Reference `OpenAICompatHTTPClient` subclass — only `complete` / `ping` / `stream` are its own |
| `src/mangomas/adapters/storage/base.py` | `TurnRepository`, `MemoryRepository` Protocols |
| `src/mangomas/adapters/storage/sqlite.py` | Reference SQLite-backed `TurnRepository` |
| `src/mangomas/adapters/storage/memory.py` | Reference file-backed `MemoryRepository` |
| `src/mangomas/secrets/provider.py` | `SecretsProvider` Protocol |
| `src/mangomas/secrets/env.py` | `EnvSecretsProvider` reference |
| `src/mangomas/composition/` | Single wiring point — `llm_registry`, `_storage_registry`, `_memory_registry`, `secrets_registry` |
| `src/mangomas/registry.py` | Generic `Registry[T]` with `scoped()` ctx manager for test isolation |
| `tests/fakes.py` | `FakeLLM`, `FakeRepository`, `FakeMemoryRepository`, `FakeSecretsProvider` — extend instead of using `mock.patch` |

---

## Template — New OpenAI-compatible HTTP Adapter

Subclass `OpenAICompatHTTPClient`. The base owns the whole httpx lifecycle, so
the subclass declares two ClassVars and implements only its call method(s).

```python
# src/mangomas/adapters/llm/<provider>.py
from __future__ import annotations

import logging
from typing import Any

import httpx

from mangomas.adapters._openai_client import OpenAICompatHTTPClient
from mangomas.config import DEFAULT_LLM_TIMEOUT_SECONDS
from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse

logger = logging.getLogger(__name__)


class <Provider>Error(LLMBadResponse):
    """Raised when <provider> returns an unexpected or malformed response."""


class <Provider>Client(OpenAICompatHTTPClient):
    """Thin OpenAI-compatible client targeting <provider>."""

    # Both are REQUIRED — __init_subclass__ raises TypeError at import if either
    # is missing, so the omission surfaces at startup, not inside a failure path.
    _LABEL = "<Provider>"
    _BAD_RESPONSE = <Provider>Error

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "...",
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Everything past `model` on the base is keyword-only — forward by keyword.
        super().__init__(
            base_url, model, api_key=api_key, timeout_seconds=timeout_seconds, client=client
        )

    async def complete(
        self, messages: list[Message], *, temperature: float | None = None
    ) -> str:
        payload: dict[str, Any] = {"model": self._model, "messages": [...]}
        try:
            resp = await self._client.post(f"{self._base_url}/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("<Provider> request failed", extra={"error": type(exc).__name__})
            # Inherited: delegates to _http_errors.translate_httpx_error with the
            # subclass's _LABEL / _BAD_RESPONSE. Never re-raise LLMTimeout by hand.
            raise self._translate_error(exc) from exc
        ...
```

Inherited from the base and therefore **not** re-implemented: `_base_url`
rstrip-normalisation, the bearer-auth `httpx.AsyncClient`, the injected-vs-owned
client flag (`_owns_client`), `_translate_error()`, and `aclose()`.

For a **non-HTTP / SDK-backed provider** (e.g. Vertex), do not subclass the base
— construct the SDK client in `__init__` (no I/O), map failures through
`_vertex_errors.translate_vertex_error(exc, project=..., bad_request_error=...)`,
and release SDK resources in your own `aclose()`. Wrap public methods in a span
via `get_tracer(__name__).start_as_current_span("adapter.<provider>.<method>")`.

Then register in the `composition/` package:

```python
from mangomas.adapters.llm.<provider> import <Provider>Client

llm_registry.register("<provider>", lambda settings: <Provider>Client(settings))
```

Activate via `MANGOMAS_LLM__PROVIDER=<provider>`.

---

## Workflow

1. Read the relevant Protocol in `adapters/*/base.py`.
2. Check for an existing shared helper before writing plumbing: `_openai_client.py`
   (OpenAI-compatible HTTP lifecycle), `_http_errors.py` / `_vertex_errors.py`
   (error translation), `embeddings/_shared.py` (embedding `embed` / `aclose` mixins).
3. Implement the adapter class in a new file under the matching adapter directory —
   subclass the shared base where one applies; only backend-specific calls are new code.
4. Add any new tunables to `mangomas.config` (`LLMSettings` in `config/llm.py`, `DBSettings` in `config/storage.py`, etc.) with `DEFAULT_*` constants.
4. Register the factory in the `composition/` package only.
5. Write `tests/test_<adapter>.py` — assert `isinstance(instance, Protocol)` and exercise success + each error path.
6. If the adapter needs a test double, extend `tests/fakes.py` (don't duplicate inline).
7. Run `ruff check --fix src tests` + `mypy --strict` + `pytest`.
8. Append a CHANGELOG entry under the unreleased section.

---

## Constraints

- DO NOT import a concrete adapter outside the `composition/` package.
- DO NOT raise bare `Exception` — use `LLMError`/`PersistenceError` subclasses.
- DO NOT call `os.environ` in the adapter — use the `SecretsProvider` seam.
- DO NOT hardcode URLs, timeouts, model names — they belong in `Settings`.
- DO NOT add a `mock.patch` on the Protocol — extend `tests/fakes.py`.
- DO NOT re-implement httpx lifecycle or `except TimeoutError: raise LLMTimeout(...)`
  by hand — subclass `OpenAICompatHTTPClient` / call the `_*_errors` translators.

---

## Diagnosing Failures

1. `UnknownProvider` at startup → the factory is not registered in the `composition/` package.
2. `isinstance(client, LLMClient)` returns `False` → a Protocol method is missing or has the wrong signature; cross-check `adapters/llm/base.py`.
3. mypy errors about `Awaitable[str]` → an async method is missing `await` or returning the coroutine itself.
4. `LLMBadResponse` in CI but not locally → upstream JSON schema drift; pin the SDK and add a regression test.
5. `TypeError: <Provider>Client must define _LABEL, _BAD_RESPONSE` at import → an
   `OpenAICompatHTTPClient` subclass omitted a required ClassVar; add both.
