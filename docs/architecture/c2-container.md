# C2 — Container Diagram

This diagram shows the major deployable / runnable units inside Mango-Mas V2
and how they communicate.

```mermaid
C4Container
  title Mango-Mas V2 — Containers

  Person(developer, "Developer / User")

  Container_Boundary(mangomas_boundary, "Mango-Mas V2") {
    Container(api, "FastAPI Application", "Python / FastAPI", "Exposes REST endpoints. Factory: create_app(). Middleware: AccessLogMiddleware, TraceMiddleware. Manages lifespan: startup wires adapters, shutdown closes clients.")
    Container(cli, "Typer CLI", "Python / Typer", "mangomas chat — single-turn interactive interface. Shares the same agent stack as the API.")
    Container(composition, "Composition Root", "Python module", "composition.py — wires LLM, storage, and agent registries at startup. No hardcoded provider classes; all resolved via Registry[T].")
  }

  System_Ext(lmstudio, "LM Studio", ":1234 — OpenAI-compatible LLM server")
  System_Ext(sqlite_file, "SQLite file", "data/mangomas.db — conversation turn store")
  System_Ext(mem_file, "File memory", "memory/ — agent memory index")
  System_Ext(otel_out, "OTel / stdout", "Traces and structured logs")

  Rel(developer, api, "POST /agents/{name}/invoke, stream; GET /healthz, /readyz, /agents", "HTTP")
  Rel(developer, cli, "mangomas chat", "shell")
  Rel(api, composition, "calls build_orchestrator() at lifespan startup")
  Rel(cli, composition, "calls build_orchestrator() at CLI startup")
  Rel(composition, lmstudio, "LMStudioClient → /v1/chat/completions, /v1/models", "HTTP")
  Rel(composition, sqlite_file, "SQLiteRepository — reads/writes turns", "SQLite driver")
  Rel(composition, mem_file, "file memory provider — reads/writes index", "filesystem")
  Rel(api, otel_out, "TraceMiddleware emits spans; structured logs via logging", "OTLP / stdout")
```

## Notes

- `api` and `cli` share the same `composition.py` wiring; no duplicated
  adapter construction.
- The `composition` container is a module, not a separate process. It is shown
  separately to emphasise that all provider-specific code is isolated there.
- `memory/` is excluded from git (see `.gitignore`).
