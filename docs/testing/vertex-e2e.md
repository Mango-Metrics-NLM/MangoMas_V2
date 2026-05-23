# Vertex AI End-to-End Scenario Plan

Six end-to-end scenarios for `VertexLLMClient` against the live Vertex
AI Gemini endpoint, mirroring `docs/testing/lmstudio-e2e.md`. All tests
are gated by `RUN_VERTEX=1` plus the `vertex` path component / marker;
none of these run in the default CI suite.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `RUN_VERTEX` | _(unset)_ | Set to `1` to enable `tests/vertex/` collection |
| `VERTEX_PROJECT` | _(required)_ | GCP project that owns the model |
| `VERTEX_LOCATION` | `us-central1` | Vertex region |
| `VERTEX_MODEL` | `gemini-1.5-flash` | Gemini model id |
| `VERTEX_BAD_MODEL` | `does-not-exist-model` | Bogus model id for scenario 6 |

Identity: Application Default Credentials. Run
`gcloud auth application-default login` locally or rely on the runtime's
ambient identity (Workload Identity Federation on GCP).

## Confidence gate before running any scenario

1. Full local quality gate passes (`ruff`, `format`, `mypy`, `pytest`,
   `check_coverage.py`).
2. `pip install -e ".[dev,vertex]"`.
3. `gcloud auth application-default login` (or equivalent ambient identity).
4. `gcloud auth application-default print-access-token` returns a token.
5. `VERTEX_PROJECT` + `VERTEX_MODEL` env vars set.

## Scenarios

### Scenario 1 — Readiness probe (`tests/vertex/test_smoke.py`)

Constructs `VertexLLMClient` directly and calls `ping()` — issues a
one-token completion (`_PING_MAX_OUTPUT_TOKENS=1`) and asserts no
exception. Lowest-cost scenario.

```
RUN_VERTEX=1 python -m pytest tests/vertex/test_smoke.py -q --no-cov
```

### Scenario 2 — Chat happy path (`tests/vertex/test_chat_invoke.py`)

`POST /agents/chat/invoke` through the FastAPI app backed by a real
`VertexLLMClient` and an in-memory SQLite repo. Asserts 200, non-empty
content, and that one turn was persisted with `agent=chat`.

### Scenario 3 — Streaming happy path (`tests/vertex/test_chat_stream.py`)

`POST /agents/chat/stream` SSE endpoint; asserts at least one token
frame matching
`{"event": "token", "data": {"content": "<text>"}, "content": "<text>"}`
and a final `{"event": "done"}` frame.

### Scenario 4 — Streaming fallback (`tests/vertex/test_stream_fallback.py`)

Uses `llm_registry.scoped("vertex", _non_streaming_factory)` to swap in
a wrapper that exposes only `complete` / `ping` / `aclose` —
`isinstance(llm, StreamingLLMClient)` is False. The streaming endpoint
falls back to buffered `complete()` and emits the
"complete() fallback" warning log.

### Scenario 5 — Summarize agent (`tests/vertex/test_summarize_invoke.py`)

Pre-populates the repo with two prior turns, then invokes the
summarize agent. Asserts 200, non-empty content, and a persisted
`agent=summarize` turn.

### Scenario 6 — Unknown model (`tests/vertex/test_unknown_model.py`)

Targets a bogus model id (`VERTEX_BAD_MODEL`, defaulting to
`does-not-exist-model`). Vertex raises a `google.api_core.exceptions`
subclass which the adapter translates to `VertexError(LLMBadResponse)`,
mapping to HTTP 502 (`llm_bad_response` error envelope).

## Implementation notes

- All tests in `tests/vertex/` are marked `@pytest.mark.vertex` (via
  `pytestmark`) and live under the `vertex` path component — the
  project's `pytest_collection_modifyitems` hook skips them unless
  `RUN_VERTEX=1`.
- `asyncio_mode = "auto"` is already set; do not add `@pytest.mark.asyncio`.
- Fixtures in `tests/vertex/conftest.py` (`vertex_project`,
  `vertex_location`, `vertex_model`, `vertex_orchestrator`, `vertex_app`)
  mirror the LM Studio E2E shape.
- The Vertex SDK is sync; the adapter wraps calls in `asyncio.to_thread`.
  A follow-up will switch to `generate_content_async` once the SDK pin
  permits.
- **Cost note:** every scenario incurs Gemini token billing. The ping
  is one token; the chat / stream / summarize scenarios are bounded by
  short prompts but still cost real cents. Use Gemini Flash (cheapest
  tier) for routine runs.

## Operational note (ADR-002)

The GCP Secret Manager backend (`GCPSecretManagerProvider`) collapses
all auth/permission/timeout failures into `None`, falling back to the
inline `api_key`. If the Vertex tests fail with credential errors,
double-check the ADR-002 fallback isn't hiding the real issue —
inspect the structured ERROR logs from `mangomas.secrets.gcp`.
