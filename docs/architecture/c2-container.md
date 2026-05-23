# C2 — Container Diagram

This diagram shows the major deployable / runnable units inside Mango-Mas V2
and how they communicate.

```mermaid
C4Container
  title Mango-Mas V2 — Containers

  Person(developer, "Developer / User")

  Container_Boundary(mangomas_boundary, "Mango-Mas V2") {
    Container(api, "FastAPI Application", "Python / FastAPI", "Exposes REST endpoints. Factory: create_app(). Middleware: AccessLogMiddleware, TraceMiddleware. Manages lifespan: startup wires adapters, shutdown closes clients.")
    Container(cli, "Typer CLI", "Python / Typer", "mangomas chat (single-turn), mangomas history (turn log), mangomas eval (offline harness). Shares the same composition root as the API.")
    Container(eval_harness, "Evaluation Harness", "Python package (src/mangomas/eval/)", "Drives a JSONL dataset through the orchestrator and aggregates per-row Scorer results. In-process; no extra runtime dependency. Surfaced via the CLI's `eval` subcommand.")
    Container(composition, "Composition Root", "Python module", "composition.py — wires LLM, storage, secrets, and agent registries at startup. No hardcoded provider classes; everything resolves through Registry[T].")
  }

  System_Ext(lmstudio, "LM Studio", ":1234 — OpenAI-compatible LLM server (default)")
  System_Ext(vertex, "Vertex AI (optional extra)", "Google Cloud Gemini endpoints via vertexai.generative_models. Selected by MANGOMAS_LLM__PROVIDER=vertex.")
  System_Ext(sqlite_file, "SQLite file", "data/mangomas.db — conversation turn store")
  System_Ext(mem_file, "File memory", "memory/ — agent memory index")
  System_Ext(eval_output, "Eval output", "eval-output/ — optional JSON reports from `mangomas eval --output-json`")
  System_Ext(otel_out, "OTel / stdout", "Traces and structured logs")

  Rel(developer, api, "POST /agents/{name}/invoke, stream; GET /healthz, /readyz, /agents", "HTTP")
  Rel(developer, cli, "mangomas chat / history / eval", "shell")
  Rel(api, composition, "calls build_orchestrator() at lifespan startup")
  Rel(cli, composition, "calls build_orchestrator() at CLI startup")
  Rel(eval_harness, composition, "constructs EvalRunner around the orchestrator")
  Rel(cli, eval_harness, "mangomas eval — loads dataset, runs scorer, writes report")
  Rel(composition, lmstudio, "LMStudioClient → /v1/chat/completions, /v1/models (when provider=lmstudio)", "HTTP")
  Rel(composition, vertex, "VertexClient → generate_content_async (when provider=vertex)", "Vertex SDK / HTTPS")
  Rel(composition, sqlite_file, "SQLiteRepository — reads/writes turns", "SQLite driver")
  Rel(composition, mem_file, "file memory provider — reads/writes index (when memory enabled)", "filesystem")
  Rel(eval_harness, eval_output, "writes JSON report when --output-json is set", "filesystem")
  Rel(api, otel_out, "TraceMiddleware emits spans; structured logs via logging", "OTLP / stdout")
```

## Notes

- `api`, `cli`, and `eval_harness` share the same `composition.py` wiring;
  no duplicated adapter construction.
- The `composition` container is a module, not a separate process. It is shown
  separately to emphasise that all provider-specific code is isolated there.
- The LLM provider is chosen at runtime through `MANGOMAS_LLM__PROVIDER`.
  Today's built-in choices are `lmstudio` (the default) and `vertex` (an
  optional extra). Adding a new provider is a registry registration in
  `composition.py` — no other container changes required.
- The evaluation harness ships its own Scorer registry
  (`mangomas.eval.scorer_registry`) parallel to the agent/LLM/storage
  registries. Built-in scorers: `exact_match`, `llm_judge`, `embedding`.
- `memory/` and `eval-output/` are excluded from git and Docker (see
  `.gitignore` / `.dockerignore`).
