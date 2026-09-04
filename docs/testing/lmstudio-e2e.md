# LM Studio End-to-End Scenario Plan

This document catalogues the twelve end-to-end scenarios for Mango-Mas V2
against a live LM Studio server.  All tests are gated by `RUN_LMSTUDIO=1` and
read server coordinates from environment variables — no model ids are
hardcoded.

Scenarios 1–6 predate the Phase-1 deliveries; **7–12 (spec-0029)** cover
streaming persistence, the per-step timeout, pipeline acceptance loops, the
shipped `plan-execute-review` graph with validation, per-agent
`MODEL_OVERRIDE`, and a composite `branch` + `fan_out` graph over HTTP.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LMSTUDIO_BASE_URL` | `DEFAULT_LLM_BASE_URL` (`http://localhost:1234/v1`) | Base URL of the LM Studio OpenAI-compatible server |
| `LMSTUDIO_MODEL` | `DEFAULT_LLM_MODEL` (`local-model`) | Model id as shown in LM Studio, e.g. `google/gemma-4-e4b` |
| `LMSTUDIO_EMBEDDING_MODEL` | `DEFAULT_EMBEDDINGS_MODEL` | Loaded embedding model id (embedding smoke test) |
| `LMSTUDIO_E2E_TIMEOUT_SECONDS` | `240` | **Adapter** budget per live call. The httpx **client** budget is derived from it (`client_timeout_for`), never below it |
| `LMSTUDIO_OVERRIDE_MODEL` | _(unset → scenario 11 skips)_ | A **second** loaded model id, for the `MODEL_OVERRIDE` scenario |

## The hardware contract (spec-0029 R2)

Every scenario here must behave identically on a GPU box and a CPU box. The
rules, and the reason each exists:

1. **No wall-clock assertions.** Budgets bound a hang; they are never an
   oracle. A CPU box is legitimately 10× slower.
2. **No exact text from a model.** Assertions are structural: non-empty
   content, JSON that parses as the agent's schema, SSE frame kinds and order,
   the persisted turn, the error envelope's `code`.
3. **Budgets come from names, never numeric literals**, and the client budget
   is derived from the adapter budget. Before spec-0029 the adapter had 240 s
   while the client wrapping it had 60 s — invisible on a GPU, and a spurious
   failure on CPU.
4. **A timeout that must fire is set below one network round trip**
   (`LIVE_STEP_TIMEOUT_SECONDS`), so it expires on every device.

`tests/tooling/test_e2e_hardware_contract.py` lints the mechanically checkable
half of this. It catches regression to known-bad shapes; it does not prove
agnosticism — that is what running the suite on both kinds of box is for.

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

### Scenario 7 — Streamed turn persists ✅ Implemented

**File:** `tests/lmstudio/test_stream_persists.py` (spec-0025)

A fully drained `POST /agents/chat/stream` leaves exactly one persisted `chat`
turn whose content equals the reassembled token stream. Chunk count and timing
are the model's business and are never asserted.

---

### Scenario 8 — Per-step timeout ✅ Implemented

**File:** `tests/lmstudio/test_step_timeout.py` (spec-0026)

`LoopSettings(step_timeout_seconds=LIVE_STEP_TIMEOUT_SECONDS)` — a budget below
one network round trip — must produce a `504` / `step_timeout` envelope with no
turn persisted. The paired negative uses the CPU-sized budget and must return
`200`, which is the assertion that would catch a mis-sized default.

---

### Scenario 9 — Pipeline acceptance loop ✅ Implemented

**File:** `tests/lmstudio/test_pipeline_acceptance.py` (spec-0027)

`dispatch_pipeline` with an always-accept predicate reports
`steps_taken == 1`; with a never-accept predicate it raises `MaxStepsExceeded`
naming the budget. Both outcomes are fixed by the predicate the test supplies,
so the model's output never decides the result.

---

### Scenario 10 — Shipped graph with validation ✅ Implemented

**File:** `tests/lmstudio/test_plan_execute_review.py`

The shipped `examples/workflows/plan-execute-review.json` with
`validate_output=True` for planner and reviewer at `temperature=0`; the final
reply must parse as `ReviewResult`.

**This is the one scenario whose result depends on model capability.** A model
that cannot emit schema-conforming JSON fails it on every device, and the
failure is a typed `LLMBadResponse` — read that as a statement about the
configured model, not about the repository: the graph, the agents and the flag
are all covered against a fake by `tests/integration/test_workflow_http_flow.py`.

---

### Scenario 11 — Per-agent `MODEL_OVERRIDE` ✅ Implemented

**File:** `tests/lmstudio/test_model_override.py` (spec-0028)

Needs a **second** loaded model. With `LMSTUDIO_OVERRIDE_MODEL` set, the chat
agent's override client is built, answers a live request, and is closed by
`aclose` alongside `ctx.llm`. Unset, the scenario skips at runtime with the
sanctioned `set LMSTUDIO_OVERRIDE_MODEL to run ...` reason. The dedup
direction (an override equal to the base model builds nothing) needs no second
model and always runs.

The two models' outputs are never compared — different models answering the
same prompt differ by design.

---

### Scenario 12 — Composite graph over HTTP ✅ Implemented

**File:** `tests/lmstudio/test_workflow_run.py` (specs 0012 / 0013)

`sequence(branch → fan_out)` posted to `POST /workflows/run` as an inline JSON
string. Routing is driven by the *input* message, which the test controls, so
it stays deterministic; the fan-out oracle is the joined arity, not elapsed
time, so a server that serialises the branches still passes.

Supersedes `scripts/run_workflow_e2e.py` as the test of this path; that script
remains the readable demo.

---

## Implementation notes

- All test files must be in `tests/lmstudio/` and marked `@pytest.mark.lmstudio`.
- `asyncio_mode = "auto"` is already set; do not add `@pytest.mark.asyncio`.
- Use `DEFAULT_LLM_BASE_URL` and `DEFAULT_LLM_MODEL` from `mangomas.config` as
  fallback defaults — never hardcode model ids.
- Clean up `LMStudioClient` instances in `finally:` blocks or via `pytest` fixtures.
- Log at `INFO` level before and after each scenario step for debug traceability.
  The `lmstudio_orchestrator` fixture logs the resolved base URL, model and
  adapter budget on every build — when a scenario fails on one machine and not
  another, that line answers the first question.
- Shared fixtures (`lmstudio_base_url`, `lmstudio_model`, `lmstudio_timeout`,
  `lmstudio_client_timeout`, `lmstudio_orchestrator`, `lmstudio_app`) live in
  `tests/lmstudio/conftest.py` and consume the env vars documented above.
- **Never pass a numeric `timeout=`.** Take `lmstudio_client_timeout`; the
  hardware-contract lint rejects a literal.

## Recording a run

"Hardware-agnostic" is a claim about two runs, not one. When running this
suite, record the model id, the hardware, and the outcome — a GPU-only green
proves only half of what the contract asserts.

| Date | Model | Hardware | Scenarios | Result |
|---|---|---|---|---|
| _(pending)_ | | GPU | 1–12 | |
| _(pending)_ | | CPU | 1–12 | |
