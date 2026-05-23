# Vertex AI LLM Adapter

`VertexClient` is the registry-loaded provider for Google Vertex AI Gemini
models. It satisfies the same `LLMClient`, `PingableLLMClient`, and
`StreamingLLMClient` protocols as the LM Studio adapter, so every agent in
the codebase works against Vertex without code changes.

The provider is selected at runtime via `MANGOMAS_LLM__PROVIDER=vertex` and
constructed by `_vertex_factory` in `src/mangomas/composition.py`.

## Install

The Vertex SDK is an **optional dependency**. Install the extra to pull it in:

```bash
pip install 'mangomas[vertex]'
```

Without the extra installed, importing `mangomas.adapters.llm.vertex` still
works (the class is always defined), but constructing a `VertexClient`
without injecting a test client raises `ImportError` pointing at the install
command above.

## Configuration

All values are environment-driven via the `MANGOMAS_` prefix and `__` nested
delimiter:

| Variable | Required? | Purpose |
|---|---|---|
| `MANGOMAS_LLM__PROVIDER` | yes | Must be `vertex` |
| `MANGOMAS_LLM__PROJECT_ID` | yes | GCP project that hosts the Vertex endpoint |
| `MANGOMAS_LLM__LOCATION` | no | GCP region (default `us-central1`) |
| `MANGOMAS_LLM__MODEL` | yes | Gemini model id, e.g. `gemini-1.5-flash` |
| `MANGOMAS_LLM__TEMPERATURE` | no | Sampling temperature (default `0.2`) |
| `MANGOMAS_LLM__TIMEOUT_SECONDS` | no | Per-request timeout (default `60.0`) |
| `MANGOMAS_LLM__CREDENTIALS_PATH` | no | Path to a service-account JSON key |
| `MANGOMAS_LLM__SECRET_REF` | no | Secrets-provider key resolving to a JSON key body |

`PROJECT_ID` has no safe default and must be set explicitly when the
provider is selected.

## Authentication

The adapter resolves credentials in this priority order:

1. **`secret_ref`** — if set, `mangomas.composition._resolve_llm_secrets`
   resolves it through `secrets_registry` (today: the env-var provider) and
   the returned string is treated as a service-account JSON body. This is
   the recommended production path because no key material touches the
   filesystem.
2. **`credentials_path`** — a path to a service-account JSON key. Used when
   `secret_ref` is unset.
3. **Application Default Credentials (ADC)** — used when both `secret_ref`
   and `credentials_path` are unset. On Cloud Run, GKE, or local
   `gcloud auth application-default login` this is usually enough.

## Error mapping

Vertex SDK exceptions are translated to the Mango-Mas typed-error vocabulary
by `_translate_vertex_error`. Translation is **qualname-based**
(`type(exc).__module__ + __qualname__`) so unit tests can exercise the
matrix without installing the Google SDK.

| Vertex exception | Typed error | HTTP status |
|---|---|---|
| `google.api_core.exceptions.DeadlineExceeded`, `RetryError` | `LLMTimeout` | 504 |
| `google.api_core.exceptions.ServiceUnavailable`, `InternalServerError`, `GatewayTimeout`, `Aborted` | `LLMUnavailable` | 502 |
| `google.auth.exceptions.RefreshError`, `DefaultCredentialsError` | `LLMUnavailable` | 502 |
| `google.api_core.exceptions.InvalidArgument`, `PermissionDenied`, `Unauthenticated`, `NotFound`, `FailedPrecondition` | `VertexError` (→ `LLMBadResponse`) | 502 |
| anything else | `LLMUnavailable` | 502 |

## Logging events

| Event (`extra.event`) | Level | Keys |
|---|---|---|
| `vertex_request` | INFO | `model`, `project`, `location`, `temperature` |
| `vertex_stream_start` | DEBUG | `model`, `project` |
| `vertex_response` | DEBUG | `model`, `duration_ms`, `content_length` |
| `vertex_chunk_skipped` | DEBUG | `model` |
| `vertex_ping_ok` | DEBUG | `model` |
| `vertex_error` | ERROR | `model`, `project`, `error_type`, `duration_ms` |

Correlation-id propagation works automatically because the adapter does not
own any cross-request state — every call inherits the current
`mangomas.correlation` ContextVar through the orchestrator and middleware.

## Streaming

`VertexClient.stream()` delegates to `GenerativeModel.generate_content_async`
with `stream=True`. Empty / safety-blocked chunks are debug-logged and
skipped so callers see only non-empty content tokens.

## Testing

- **Unit**: `tests/test_vertex_unit.py` drives the adapter with
  `FakeVertexGenerativeModel` from `tests/fakes.py`. No SDK install required.
- **Composition**: `tests/test_composition.py` verifies the `vertex` key is
  registered and that `_vertex_factory` produces a `VertexClient`.
- **E2E**: `tests/vertex/` runs against a real Vertex project. Gated by
  `RUN_VERTEX=1`; project / location / model are supplied via env vars
  (see "Configuration"). Skip is automatic when `VERTEX_PROJECT_ID` is
  absent.

```bash
RUN_VERTEX=1 \
  VERTEX_PROJECT_ID=my-gcp-project \
  VERTEX_LOCATION=us-central1 \
  VERTEX_MODEL=gemini-1.5-flash \
  python -m pytest tests/vertex --no-cov -q
```

## Known gaps and follow-ups

- The adapter does not yet expose an `embed()` method. The evaluation
  harness's `EmbeddingScorer` skips with a clear warning when used against
  Vertex — to be revisited when the eval harness lands an embedding path.
- `vertexai.init()` mutates module-level SDK state. Building multiple
  `VertexClient` instances for different projects in the same process is
  not yet validated — each `init()` call overwrites the prior one. Fine
  for the single-tenant deployment model in v0.3.0; revisit for multi-tenancy.
