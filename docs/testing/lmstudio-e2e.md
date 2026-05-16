# LM Studio End-to-End Scenario Plan

This document catalogues the six planned end-to-end scenarios for Mango-Mas V2
against a live LM Studio server.  All tests are gated by `RUN_LMSTUDIO=1` and
read server coordinates from environment variables — no model ids are
hardcoded.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LMSTUDIO_BASE_URL` | `DEFAULT_LLM_BASE_URL` (`http://localhost:1234/v1`) | Base URL of the LM Studio OpenAI-compatible server |
| `LMSTUDIO_MODEL` | `DEFAULT_LLM_MODEL` (`local-model`) | Model id as shown in LM Studio, e.g. `google/gemma-4-e4b` |

## Confidence gate before running any scenario

1. Full local quality gate passes (`ruff`, `format`, `mypy`, `pytest`, `check_coverage.py`).
2. LM Studio is running and the target model is loaded.
3. `curl http://localhost:1234/v1/models` returns 200 with model id in the response.
4. `RUN_LMSTUDIO=1 python -m pytest tests/lmstudio --no-cov -q` passes (ping smoke test).

---

## Scenarios

### Scenario 1 — Readiness probe ✅ Implemented (`test_lmstudio_ping`)

**File:** `tests/lmstudio/test_smoke.py`

**What is tested:**  
`LMStudioClient.ping()` calls `GET {base_url}/models` and succeeds without
raising.

**Assertions:**
- No exception raised.
- Debug log `"LM Studio ping OK"` present.

**Gate:** `RUN_LMSTUDIO=1`

---

### Scenario 2 — Chat happy path ✅ Implemented

**File:** `tests/lmstudio/test_chat_invoke.py`

**What is tested:**  
`POST /agents/chat/invoke` with a single user turn against the full FastAPI
app backed by a real `LMStudioClient` and an in-memory `FakeRepository`.

**Setup:**
```python
client = LMStudioClient(base_url=_base_url(), model=_model())
orch = Orchestrator(AgentContext(llm=client, repo=FakeRepository()))
orch.register(ChatAgent())
app = create_app(orchestrator=orch)
```

**Assertions:**
- HTTP 200.
- `response.json()["content"]` is a non-empty string.
- Exactly one turn persisted via `FakeRepository.list_turns()`.
- Turn `agent` field equals `"chat"`.

---

### Scenario 3 — Streaming happy path ✅ Implemented

**File:** `tests/lmstudio/test_chat_stream.py`

**What is tested:**  
`POST /agents/chat/stream` SSE endpoint; token delivery and sentinel frame.

**Setup:** Same as Scenario 2 but using `httpx.AsyncClient` with
`httpx.ASGITransport` and reading the streaming response line by line.

**Assertions:**
- At least one SSE frame matching
  `{"event": "token", "data": {"content": "<non-empty>"}, "content": "<non-empty>"}`.
- Final frame is `{"event": "done"}`.
- HTTP 200 with `Content-Type: text/event-stream`.

---

### Scenario 4 — Streaming fallback warning ✅ Implemented

**File:** `tests/lmstudio/test_stream_fallback.py` (uses `Registry.scoped()`
to swap in a `NonStreamingLMStudioClient` wrapper for the test's duration)

**What is tested:**  
The streaming endpoint falls back to buffered `complete()` and emits a warning
log when the active LLM does not implement `StreamingLLMClient`.

**Setup:** Wrap `LMStudioClient` in a non-streaming adapter (or use
`NonPingableFakeLLM` equivalent) so `isinstance(llm, StreamingLLMClient)`
returns `False`.

**Assertions:**
- Response still arrives and contains a non-empty token frame.
- Warning log line with `"LLM does not support streaming"` (or equivalent).

---

### Scenario 5 — Summarize agent with persisted turns ✅ Implemented

**File:** `tests/lmstudio/test_summarize_invoke.py`

**What is tested:**  
`POST /agents/summarize/invoke` with a multi-message thread; the summarize
agent fetches prior turns from the repository and generates a summary.

**Setup:**
```python
# Pre-populate the repository with two prior turns, then invoke summarize
```

**Assertions:**
- HTTP 200.
- `response.json()["content"]` is a non-empty string.
- Persisted turn `agent` field equals `"summarize"`.

---

### Scenario 6 — Error path: unavailable / bogus model ✅ Implemented

**File:** `tests/lmstudio/test_unknown_model.py`

**What is tested:**  
`POST /agents/chat/invoke` when `LMSTUDIO_MODEL` points to a model id that is
not loaded in LM Studio.  The adapter raises `LMStudioError` (subclass of
`LLMBadResponse`) which maps to `502 Bad Gateway` via `_error_status`.

**Setup:**
```python
# Use a deliberately invalid model id, e.g. "does-not-exist-model"
# (read from a dedicated env var LMSTUDIO_BAD_MODEL, default "does-not-exist")
```

**Assertions:**
- HTTP 502.
- `response.json()["error"]` equals `"llm_bad_response"` (or the `LMStudioError.code`).
- Structured error log line with `"Malformed LM Studio response"` or
  `"Request error"` present in logs.

---

## Implementation notes

- All test files must be in `tests/lmstudio/` and marked `@pytest.mark.lmstudio`.
- `asyncio_mode = "auto"` is already set; do not add `@pytest.mark.asyncio`.
- Use `DEFAULT_LLM_BASE_URL` and `DEFAULT_LLM_MODEL` from `mangomas.config` as
  fallback defaults — never hardcode model ids.
- Clean up `LMStudioClient` instances in `finally:` blocks or via `pytest` fixtures.
- Log at `INFO` level before and after each scenario step for debug traceability.
- Shared fixtures (`lmstudio_base_url`, `lmstudio_model`, `lmstudio_orchestrator`,
  `lmstudio_app`) live in `tests/lmstudio/conftest.py` and consume the env vars
  documented above.
