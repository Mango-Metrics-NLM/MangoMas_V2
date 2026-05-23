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

  System_Ext(sqlite, "SQLite", "Local file-based relational database used for conversation turn persistence.")

  System_Ext(otel, "OpenTelemetry Collector / stdout", "Receives traces and structured log output from the application.")

  System_Ext(gcp_future, "Google Cloud Platform (remaining future targets)", "Planned swap-in targets still pending: Cloud SQL/Postgres (storage), Secret Manager (secrets), Cloud Run (compute), Cloud Logging + Trace (observability). See ADR-001.")

  Rel(developer, mangomas, "Invokes agents / queries health / runs eval", "HTTP REST or CLI")
  Rel(mangomas, lmstudio, "Sends chat completion requests", "HTTP (OpenAI-compat /v1/chat/completions)")
  Rel(mangomas, vertex, "Sends generate_content requests (when provider=vertex)", "Vertex SDK / HTTPS")
  Rel(mangomas, sqlite, "Reads and writes conversation turns", "SQLite driver")
  Rel(mangomas, otel, "Emits traces and structured logs", "OTLP / stdout")
  Rel_Back(gcp_future, mangomas, "Future: swap in remaining GCP boundaries via existing registry seams", "TLS / IAM")
```

## Notes

- LM Studio is the only external **process** dependency for local development.
  Vertex AI is an external **service** consumed only when the `vertex`
  provider is selected.
- All external service coordinates are env-driven (`MANGOMAS_*` prefix); no
  endpoint, project id, or model id is hardcoded in source.
- The Vertex AI swap-in (ADR-001) shipped in v0.3.0; remaining GCP targets
  (Postgres, Secret Manager, Cloud Run, Cloud Logging/Trace) are still
  tracked in [NEXT_STEPS.md](../../NEXT_STEPS.md). No GCP resources are
  provisioned by this branch.
- The **evaluation harness** runs entirely in-process; it consumes the same
  `Orchestrator` / `LLMClient` stack as `/agents/{name}/invoke`. It does
  not introduce a new external dependency. See
  [docs/eval/harness.md](../eval/harness.md).
