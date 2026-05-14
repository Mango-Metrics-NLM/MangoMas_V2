# ADR-001: Cloud Target Swap Matrix

## Status

Accepted.

## Context

Mango-Mas currently runs as a local-first FastAPI service backed by LM Studio and SQLite. The codebase needs to remain deployable locally while leaving clear seams for a later GCP migration without rewriting the core agent contracts.

## Decision

Keep provider-specific code behind existing protocol and registry boundaries. Future cloud providers are added as registry entries rather than changing orchestrator, agent, or API contracts.

| Boundary | Current | Future GCP Target | Swap Mechanism |
| --- | --- | --- | --- |
| LLM | LM Studio OpenAI-compatible API | Vertex AI Gemini | Add a `vertex` LLM registry provider implementing `LLMClient` and optional `StreamingLLMClient`. |
| Storage | SQLite | Cloud SQL Postgres | Add a `postgres` storage registry provider implementing `TurnRepository`. |
| Secrets | `.env` / process env | Secret Manager | Add a `SecretsProvider` abstraction consumed by settings construction. |
| Telemetry | stdout logs + console trace exporter | Cloud Logging + Cloud Trace | Swap telemetry exporter through configuration while preserving structured log fields. |
| Compute | Docker Compose / local uvicorn | Cloud Run | Keep container non-root, `$PORT` aware, and probe `/healthz`. |
| Identity | Local credentials | Workload Identity Federation | Provider adapters consume ambient identity, not hardcoded credentials. |

## Consequences

- Core agents and orchestrator remain cloud-agnostic.
- New providers must pass the same protocol tests as local providers.
- Cloud-specific concerns stay in adapters, configuration, deployment docs, and CI validation.
- No GCP resources are provisioned by this branch.
