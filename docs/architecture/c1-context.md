# C1 — System Context

This diagram shows Mango-Mas V2 in its broader environment: who uses it and
what external systems it depends on.

```mermaid
C4Context
  title Mango-Mas V2 — System Context

  Person(developer, "Developer / User", "Runs queries via the REST API, the chat CLI, or the eval CLI.")

  System(mangomas, "Mango-Mas V2", "Local-first modular agent platform. Orchestrates LLM agents, persists conversation turns, exposes a FastAPI service, and runs offline evaluation through a JSONL dataset harness.")

  System_Ext(lmstudio, "LM Studio", "Local LLM server exposing an OpenAI-compatible API on :1234. Default provider for development.")

  System_Ext(vertex, "Vertex AI (optional)", "Google Cloud Gemini endpoints via vertexai.generative_models. Activated by MANGOMAS_LLM__PROVIDER=vertex and the `mangomas[vertex]` optional extra.")

  System_Ext(sqlite, "SQLite", "Local file-based relational database used for conversation turn persistence (default).")

  System_Ext(postgres, "PostgreSQL (GCP Cloud SQL)", "Cloud SQL relational database. Activated by MANGOMAS_DB__PROVIDER=postgres; uses asyncpg connection pool. Requires the `mangomas[postgres]` optional extra.")

  System_Ext(secret_mgr, "GCP Secret Manager", "Resolves API keys and service-account JSON at orchestrator-build time. Activated by MANGOMAS_SECRETS__PROVIDER=gcp. Fails soft to None by default (ADR-0002); MANGOMAS_SECRETS__STRICT=true raises SecretsResolutionError (503) instead, so a misconfigured deployment is loud rather than silently unauthenticated (spec-0003).")

  System_Ext(otel, "OpenTelemetry Collector / stdout", "Receives traces, metrics and structured log output. Span exporter selected by MANGOMAS_TELEMETRY__EXPORTER (console | gcp Cloud Trace, ADR-0009); an opt-in MeterProvider emits agent invocation/error/duration metrics when MANGOMAS_TELEMETRY__METRICS_ENABLED=true (ADR-0013).")

  System_Ext(cloud_run, "Cloud Run", "Container compute target. deploy/service.yaml + .github/workflows/deploy.yml build and deploy the image; the runtime is the same FastAPI app with MANGOMAS_ENV=prod and JSON logs (spec-0004).")

  Rel(developer, mangomas, "Invokes agents / queries health / runs eval", "HTTP REST or CLI")
  Rel(mangomas, lmstudio, "Sends chat completion requests", "HTTP (OpenAI-compat /v1/chat/completions)")
  Rel(mangomas, vertex, "Sends generate_content requests (when provider=vertex)", "Vertex SDK / HTTPS")
  Rel(mangomas, sqlite, "Reads and writes conversation turns (default)", "SQLite driver")
  Rel(mangomas, postgres, "Reads and writes conversation turns (when provider=postgres)", "asyncpg / TLS")
  Rel(mangomas, secret_mgr, "Resolves secret references (when provider=gcp)", "Secret Manager API / IAM")
  Rel(mangomas, otel, "Emits traces, metrics and structured logs", "OTLP / stdout")
  Rel(mangomas, cloud_run, "Deployed as a container image (make deploy / deploy.yml)", "Cloud Run / HTTPS")
```

## Notes

- LM Studio is the only external **process** dependency for local development.
  Vertex AI is an external **service** consumed only when the `vertex`
  provider is selected.
- All external service coordinates are env-driven (`MANGOMAS_*` prefix); no
  endpoint, project id, or model id is hardcoded in source.
- Every GCP boundary this diagram once listed as "future" has landed, each
  through an existing registry seam rather than a rewrite: Vertex AI (ADR-001,
  v0.3.0), PostgreSQL via asyncpg and GCP Secret Manager (v0.3.1, both E2E
  verified), the Cloud Trace span exporter (ADR-0009 / spec-0001) and Cloud Run
  (spec-0004). What remains open is tracked in
  [NEXT_STEPS.md](../../NEXT_STEPS.md) — no external system on this diagram is
  aspirational.
- Multi-tenancy (ADR-0017) adds no external system. `TenancyMiddleware` reads
  an inbound header into a `ContextVar` that the storage adapters filter rows
  on, so the tenant boundary is enforced inside the existing SQLite/Postgres
  systems rather than by a new one. Default-OFF.
- Vertex AI adapter code is functional but project-level model access is
  currently blocked on the GCP side; LM Studio + GCP Secret Manager
  integration is E2E verified.
- The **evaluation harness** runs entirely in-process; it consumes the same
  `Orchestrator` / `LLMClient` stack as `/agents/{name}/invoke`. It does
  not introduce a new external dependency. See
  [docs/eval/harness.md](../eval/harness.md).
