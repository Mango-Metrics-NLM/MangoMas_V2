# Cloud Providers

Mango-Mas V2 v0.3.0 ships three GCP-target adapters that complete the
ADR-001 swap matrix. All three follow the same pattern:

1. **Lazy SDK import** — every `google.*`, `vertexai.*`, and `asyncpg`
   import lives inside function bodies (factory bodies in
   `composition/`, method bodies in the adapter modules). The
   adapter modules are importable even when the optional extra is not
   installed; the SDK is only resolved when the corresponding provider
   is actually selected via `Settings`.
2. **Constructor-injection test seam** — every adapter accepts a
   `client=` (or pool / model) parameter so unit tests can substitute
   a fake without ever touching the real SDK.
3. **Ambient identity only** — credentials flow from Application
   Default Credentials / Workload Identity Federation. Service-account
   JSON keys are **never** accepted by configuration, read from env,
   or written to disk.

| Boundary | Adapter | Selected by | Optional extra |
|---|---|---|---|
| LLM | `mangomas.adapters.llm.vertex.VertexClient` | `MANGOMAS_LLM__PROVIDER=vertex` | `pip install -e ".[vertex]"` |
| Storage | `mangomas.adapters.storage.postgres.PostgresRepository` | `MANGOMAS_DB__PROVIDER=postgres` | `pip install -e ".[postgres]"` |
| Secrets | `mangomas.secrets.gcp.GCPSecretManagerProvider` | `MANGOMAS_SECRETS__PROVIDER=gcp` | `pip install -e ".[gcp]"` |

The meta-extra `pip install -e ".[cloud]"` installs all three.

---

## Vertex AI (Gemini)

### Configuration

| Env var | Default | Notes |
|---|---|---|
| `MANGOMAS_LLM__PROVIDER` | `lmstudio` | Set to `vertex` to activate |
| `MANGOMAS_LLM__PROJECT_ID` | _(none)_ | **Required.** GCP project that owns the model |
| `MANGOMAS_LLM__LOCATION` | `us-central1` | Vertex region |
| `MANGOMAS_LLM__MODEL` | `local-model` | Gemini id, e.g. `gemini-1.5-flash` |
| `MANGOMAS_LLM__TIMEOUT_SECONDS` | `60.0` | Per-request deadline |
| `MANGOMAS_LLM__TEMPERATURE` | `0.2` | Sampling temperature |

### Registration

`composition.llm._vertex_factory` is registered unconditionally into
`llm_registry` as `"vertex"`. The Vertex SDK import only fires when the
factory is invoked (`llm_registry.get(cfg.llm.provider)(cfg.llm)`).

### Exception mapping

| Google SDK exception | MangoMas error | HTTP status |
|---|---|---|
| `google.auth.exceptions.DefaultCredentialsError` | `LLMUnavailable` | 503 |
| `google.api_core.exceptions.DeadlineExceeded` / `RetryError` | `LLMTimeout` | 504 |
| `google.api_core.exceptions.ServiceUnavailable` / `Aborted` | `LLMUnavailable` | 503 |
| Other `google.api_core.exceptions.GoogleAPIError` | `VertexError` (`LLMBadResponse`) | 502 |
| Anything else | `LLMUnavailable` | 503 |

### Notes

- The Vertex SDK is synchronous; calls are wrapped in
  `asyncio.to_thread`. A v0.4.0 follow-up will switch to
  `generate_content_async` when the SDK pin permits.
- `vertexai.init()` mutates a process-global. v0.3.0 constructs one
  `VertexLLMClient` per orchestrator (single-tenant per process); the
  multi-tenant story is on the long-term roadmap.
- `ping()` issues a one-token `generate_content` — operators running
  aggressive `/readyz` probes against Vertex should widen probe
  intervals or implement a cheaper liveness signal upstream. A
  zero-token metadata-fetch ping is tracked as a v0.4.0 improvement.

---

## Postgres (Cloud SQL)

### Configuration

| Env var | Default | Notes |
|---|---|---|
| `MANGOMAS_DB__PROVIDER` | `sqlite` | Set to `postgres` to activate |
| `MANGOMAS_DB__URL` | `sqlite:///./data/mangomas.db` | asyncpg DSN |
| `MANGOMAS_DB__POOL_MIN` | `1` | asyncpg pool minimum |
| `MANGOMAS_DB__POOL_MAX` | `10` | asyncpg pool maximum |
| `MANGOMAS_DB__CONNECT_TIMEOUT_SECONDS` | `10.0` | Connection acquisition timeout |
| `MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS` | _(none)_ | Per-statement timeout |

### Registration

`composition.storage._postgres_factory` is registered unconditionally into
`_storage_registry` as `"postgres"`. The asyncpg import only fires when
the pool is created (`_ensure_pool`).

### Pool lifecycle

- `__init__` is sync and does **no I/O** — preserves the
  `_sqlite_factory(cfg: DBSettings) -> SQLiteRepository` factory shape
  used everywhere in `composition/`.
- `_ensure_pool` is an async lazy initialiser guarded by an
  `asyncio.Lock` (init only — per-query access is pool-managed,
  lock-free).
- `aclose()` is the preferred teardown path; the FastAPI lifespan and
  CLI close path both prefer it via the
  `AsyncCloseableRepository` extension protocol.
- A best-effort sync `close()` exists for non-loop callers and uses
  `pool.terminate()`.

### Row-shape parity

asyncpg returns `jsonb` columns as `str` by default. To preserve
protocol parity with `SQLiteRepository` (which returns dicts via
`json.loads`), the pool's `init` hook registers a `json.dumps` /
`json.loads` codec on every connection.

---

## GCP Secret Manager

### Configuration

| Env var | Default | Notes |
|---|---|---|
| `MANGOMAS_SECRETS__PROVIDER` | `env` | Set to `gcp` to activate |
| `MANGOMAS_SECRETS__PROJECT_ID` | _(none)_ | **Required.** GCP project that owns the secrets |
| `MANGOMAS_SECRETS__TIMEOUT_SECONDS` | `5.0` | `access_secret_version` deadline |
| `MANGOMAS_SECRETS__DEFAULT_VERSION` | `latest` | Version suffix for short ids |
| `MANGOMAS_LLM__SECRET_REF` | _(none)_ | Short id (`api-key`) or full resource path |

### Registration

`secrets_registry` stores **instances**, not factories (the env
backend has no config). The GCP provider needs `project_id` from
`SecretsSettings`, so `build_orchestrator` constructs it lazily and
registers it before `_resolve_llm_secrets` runs. The registration is
idempotent — repeated `build_orchestrator` calls are safe.

### Error semantics (ADR-002)

All failure modes — `NotFound`, `DefaultCredentialsError`,
`PermissionDenied`, `Unauthenticated`, `DeadlineExceeded`, any other
`GoogleAPIError` — collapse to `None`. The composition layer
(`composition.secrets._resolve_llm_secrets`) falls back to the inline
`api_key` setting. Auth failures emit ERROR-level structured logs
with `extra={"error": ..., "project_id": ..., "secret_name": ...}`;
`NotFound` emits a DEBUG log only.

**Operational alert:** the only signal that a rotated secret has
silently degraded to a stale inline `api_key` is the ERROR log line.
Operators MUST alert on
`logger=mangomas.secrets.gcp severity=ERROR`.

The v0.4.0 roadmap adds `SecretsSettings.strict: bool = False` for an
opt-in "fail loud" mode in production.

### Logging safety

- Secret values are **never** logged.
- Full resource paths (with version) are **never** logged — only the
  short id appears in `secret_name`.
- DSN passwords are **never** logged — Postgres logs only `dsn_host`.

---

## Identity (Workload Identity Federation)

All three providers consume **ambient identity** via Application
Default Credentials. Locally, `gcloud auth application-default login`
provides credentials; on GCP compute (GKE, Cloud Run, Cloud
Functions), Workload Identity Federation provides them at the metadata
endpoint.

The codebase does not accept service-account JSON keys via env, config,
or file paths. This is enforced by code review, not by a runtime check —
adding such a path would require a new ADR justifying it.

---

## Adding a fourth provider

The Cloud Trace exporter has since landed
(`MANGOMAS_TELEMETRY__EXPORTER=gcp`, lazy `CloudTraceSpanExporter` in
`telemetry/exporters.py`) — as an exporter seam inside `telemetry/`, not
as a fourth adapter, so it added no pressure toward a shared adapter base
class. Where the rule-of-three threshold *was* met, shared helpers were
extracted: `adapters/_openai_client.py` (`OpenAICompatHTTPClient`, the
shared httpx lifecycle for the OpenAI-compatible clients),
`adapters/_http_errors.py` (httpx → typed-error translation), and
`adapters/_vertex_errors.py` (the Vertex qualname error matrix shared by
llm + embeddings). Provider bodies that remained sufficiently different
(asyncpg vs. Google SDK; raise vs. return-None semantics) stay separate.

To add a new provider today:

1. Implement the adapter module under `src/mangomas/adapters/<layer>/`
   or `src/mangomas/secrets/`. SDK imports must live inside function
   bodies.
2. Add a factory in `composition/` and register it under its
   provider name.
3. Add a paired unit-test file under `tests/` that uses the
   constructor-injection seam to avoid the SDK at test time.
4. Add a gated E2E suite under `tests/<provider>/` if it's a live
   cloud surface.
5. Update this doc.
