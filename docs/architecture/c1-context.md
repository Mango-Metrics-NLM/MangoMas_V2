# C1 — System Context

This diagram shows Mango-Mas V2 in its broader environment: who uses it and
what external systems it depends on.

```mermaid
C4Context
  title Mango-Mas V2 — System Context

  Person(developer, "Developer / User", "Runs queries via the REST API or CLI.")

  System(mangomas, "Mango-Mas V2", "Local-first modular agent platform. Orchestrates LLM agents, persists conversation turns, and exposes a FastAPI service.")

  System_Ext(lmstudio, "LM Studio", "Local LLM server exposing an OpenAI-compatible API on :1234. Hosts models such as google/gemma-4-e4b.")

  System_Ext(sqlite, "SQLite", "Local file-based relational database used for conversation turn persistence.")

  System_Ext(otel, "OpenTelemetry Collector / stdout", "Receives traces and structured log output from the application.")

  System_Ext(gcp_future, "Google Cloud Platform (future)", "Planned swap-in targets: Vertex AI (LLM), Cloud SQL/Postgres (storage), Secret Manager (secrets), Cloud Run (compute), Cloud Logging + Trace (observability). See ADR-001.")

  Rel(developer, mangomas, "Invokes agents / queries health", "HTTP REST or CLI")
  Rel(mangomas, lmstudio, "Sends chat completion requests", "HTTP (OpenAI-compat /v1/chat/completions)")
  Rel(mangomas, sqlite, "Reads and writes conversation turns", "SQLite driver")
  Rel(mangomas, otel, "Emits traces and structured logs", "OTLP / stdout")
  Rel_Back(gcp_future, mangomas, "Future: replaces LM Studio, SQLite, and stdout exporters", "TLS / IAM")
```

## Notes

- LM Studio is the only external process dependency for local development.
- All external service coordinates are env-driven (`MANGOMAS_*` prefix).
- GCP targets are documented in [ADR-001](../adr/0001-cloud-targets.md) and
  tracked in [NEXT_STEPS.md](../../NEXT_STEPS.md). No GCP resources are
  provisioned by this branch.
