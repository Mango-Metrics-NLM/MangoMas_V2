# C2 — Container Diagram

This diagram shows the major deployable / runnable units inside Mango-Mas V2
and how they communicate.

```mermaid
C4Container
  title Mango-Mas V2 — Containers

  Person(developer, "Developer / User")

  Container_Boundary(mangomas_boundary, "Mango-Mas V2") {
    Container(api, "FastAPI Application", "Python / FastAPI", "Exposes REST endpoints. Factory: create_app(). Middleware: AccessLogMiddleware, TraceMiddleware. Manages lifespan: startup wires adapters, shutdown closes clients.")
    Container(cli, "Typer CLI", "Python / Typer", "mangomas chat (single-turn), mangomas history (turn log), mangomas eval (offline harness), mangomas rag ingest|query, mangomas workflow validate|run. Shares the same composition root as the API.")
    Container(eval_harness, "Evaluation Harness", "Python package (src/mangomas/eval/)", "Drives a JSONL dataset through the orchestrator and aggregates per-row Scorer results. In-process; no extra runtime dependency. Surfaced via the CLI's `eval` subcommand.")
    Container(rag, "RAG Layer (opt-in)", "Python package (src/mangomas/rag/)", "Pure-domain retrieval-augmented generation: chunker, loader, IngestionPipeline, Retriever, RetrievalTool. Imports only the EmbeddingClient / VectorStoreRepository protocols. Surfaced via `mangomas rag ingest|query` and wired into ToolAgent via ctx.tools. Dormant unless MANGOMAS_EMBEDDINGS__ENABLED + MANGOMAS_VECTOR__ENABLED.")
    Container(workflow, "Workflow Graph Layer (opt-in)", "Python package (src/mangomas/workflow/)", "Declarative multi-agent topologies: a frozen WorkflowGraph (agent / sequence / fan_out / loop / branch) compiled to the Orchestrator's public dispatch primitives — every leaf is one dispatch call, so an all-agent sequence equals dispatch_pipeline. Surfaced via POST /workflows/run|validate and `mangomas workflow validate|run`. Dormant unless MANGOMAS_WORKFLOW__ENABLED or an explicit --definition.")
    Container(composition, "Composition Root", "Python module", "composition.py — wires LLM, storage, secrets, embeddings, vector, agent, and harness registries at startup. Returns _HarnessOrchestrator when MANGOMAS_HARNESS__ENABLED=true; otherwise a plain Orchestrator. No hardcoded provider classes.")
    Container(harness, "Claude Code Harness (opt-in)", "Project-scoped harness config", "scripts/lint_agent_frontmatter.py (CI + pre-commit gate over .github/agents and .claude/skills), scripts/harness_session_start.py (SessionStart probe — venv + LM Studio reachability), .claude/settings.json (Allow/Deny perms, Stop/PostToolUse hooks). Dormant when harness.enabled=False.")
  }

  System_Ext(lmstudio, "LM Studio", ":1234 — OpenAI-compatible LLM server (default)")
  System_Ext(vertex, "Vertex AI (optional extra)", "Google Cloud Gemini endpoints via vertexai.generative_models. Selected by MANGOMAS_LLM__PROVIDER=vertex.")
  System_Ext(sqlite_file, "SQLite file", "data/mangomas.db — conversation turn store (default)")
  System_Ext(postgres_db, "PostgreSQL (Cloud SQL)", "Cloud SQL via asyncpg connection pool. Activated by MANGOMAS_DB__PROVIDER=postgres.")
  System_Ext(secret_mgr, "GCP Secret Manager", "Resolves API keys / SA JSON at build time. Activated by MANGOMAS_SECRETS__PROVIDER=gcp.")
  System_Ext(mem_file, "File memory", "memory/ — agent memory index")
  System_Ext(eval_output, "Eval output", "eval-output/ — optional JSON reports from `mangomas eval --output-json`")
  System_Ext(embed_backend, "Embedding backend (opt-in)", "LM Studio /v1/embeddings (default), in-process sentence-transformers, or Vertex text-embedding-004. Selected by MANGOMAS_EMBEDDINGS__PROVIDER.")
  System_Ext(chroma_store, "Chroma vector store (opt-in)", "data/chroma — persistent ChromaDB collection (hnsw:space=cosine). Activated by MANGOMAS_VECTOR__ENABLED.")
  System_Ext(otel_out, "OTel / stdout", "Traces and structured logs")

  Rel(developer, api, "POST /agents/{name}/invoke, stream; GET /healthz, /readyz, /agents", "HTTP")
  Rel(developer, cli, "mangomas chat / history / eval", "shell")
  Rel(api, composition, "calls build_orchestrator() at lifespan startup")
  Rel(cli, composition, "calls build_orchestrator() at CLI startup")
  Rel(eval_harness, composition, "constructs EvalRunner around the orchestrator")
  Rel(cli, eval_harness, "mangomas eval — loads dataset, runs scorer, writes report")
  Rel(composition, lmstudio, "LMStudioClient → /v1/chat/completions, /v1/models (when provider=lmstudio)", "HTTP")
  Rel(composition, vertex, "VertexClient → generate_content_async (when provider=vertex)", "Vertex SDK / HTTPS")
  Rel(composition, sqlite_file, "SQLiteRepository — reads/writes turns (when provider=sqlite)", "SQLite driver")
  Rel(composition, postgres_db, "PostgresRepository — reads/writes turns (when provider=postgres)", "asyncpg / TLS")
  Rel(composition, secret_mgr, "GCPSecretManagerProvider — resolves secret refs (when provider=gcp)", "Secret Manager API / IAM")
  Rel(composition, mem_file, "file memory provider — reads/writes index (when memory enabled)", "filesystem")
  Rel(api, workflow, "POST /workflows/run|validate — load_workflow + execute_workflow (opt-in)")
  Rel(cli, workflow, "mangomas workflow validate|run -f graph.json")
  Rel(workflow, composition, "executes against the orchestrator's public dispatch surface")
  Rel(composition, rag, "constructs IngestionPipeline + Retriever + RetrievalTool (when embeddings + vector enabled)")
  Rel(cli, rag, "mangomas rag ingest|query — load/chunk/embed/upsert, then embed-query/search")
  Rel(rag, embed_backend, "EmbeddingClient.embed_batch (when provider=lmstudio → HTTP; sentence_transformers → in-process; vertex → SDK)")
  Rel(rag, chroma_store, "VectorStoreRepository.upsert/query/delete_by_source", "chromadb / filesystem")
  Rel(eval_harness, embed_backend, "EmbeddingScorer cosine scoring via ctx.embeddings (when embeddings enabled)")
  Rel(eval_harness, eval_output, "writes JSON report when --output-json is set", "filesystem")
  Rel(api, otel_out, "TraceMiddleware emits spans; structured logs via logging", "OTLP / stdout")
  Rel(composition, harness, "Engages _HarnessOrchestrator wrapper + emits harness.agent_invoke spans (when harness.enabled=true)")
  Rel(harness, otel_out, "harness.agent_invoke parent spans + JSON structured logs", "OTLP / stdout")
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
- The storage provider is chosen via `MANGOMAS_DB__PROVIDER`: `sqlite`
  (default) or `postgres` (asyncpg pool, requires `mangomas[postgres]`).
- The secrets provider is chosen via `MANGOMAS_SECRETS__PROVIDER`: `env`
  (default) or `gcp` (GCP Secret Manager, E2E verified in v0.3.1).
- The evaluation harness ships its own Scorer registry
  (`mangomas.eval.scorer_registry`) parallel to the agent/LLM/storage
  registries. Built-in scorers: `exact_match`, `regex_match`, `contains`,
  `json_keys`, `llm_judge`, `embedding`.
  The `embedding` scorer prefers `ctx.embeddings` and falls back to an
  `embed`-capable LLM; it raises only when neither is available.
- The **RAG layer** is opt-in and dual-gated: `ctx.embeddings` is attached
  when `MANGOMAS_EMBEDDINGS__ENABLED=true` and `ctx.vector_store` when
  `MANGOMAS_VECTOR__ENABLED=true`. The `RetrievalTool` is wired into
  `ctx.tools` only when **both** are present, so `ToolAgent` auto-discovers
  it without any new plumbing. The embedding backend is provider-pluggable
  (`lmstudio` | `sentence_transformers` | `vertex`); the vector store is
  Chroma with `hnsw:space=cosine` (similarity `1 - distance/2`). Chroma and
  sentence-transformers SDKs are optional extras (`mangomas[rag]`,
  `mangomas[embeddings-local]`), lazy-imported so the modules stay importable
  without them. With both flags `false` (the default) there is no
  behaviour change and no heavy dependency is touched.
- The Claude Code harness container is opt-in: with
  `MANGOMAS_HARNESS__ENABLED=false` (the default), the
  `_HarnessOrchestrator` wrapper is never engaged and the box is
  effectively absent. Skills, sub-agents, and the frontmatter linter
  remain installed but inert at runtime — they're consumed by the
  Claude Code IDE/web client and CI, not the FastAPI process.
- `memory/`, `eval-output/`, and harness scratch state
  (`.claude/cache/`, `.claude/state/`, `.claude/logs/`,
  `.claude/settings.local.json`) are excluded from git and Docker
  (see `.gitignore` / `.dockerignore`).
