# C3 — Component Diagram (FastAPI Application)

This diagram shows the components inside the FastAPI Application container and
how they interact at the class / module level.

```mermaid
C4Component
  title Mango-Mas V2 — FastAPI Application Components

  Container_Boundary(api_boundary, "FastAPI Application (src/mangomas/api/)") {
    Component(app_factory, "create_app()", "FastAPI factory function", "Constructs and configures the FastAPI app. Wires middleware, exception handlers, and routes. Calls ensure_secrets_provider() and then resolve_auth_state() during construction — the provider must be registered first, because the lifespan's build_orchestrator() runs strictly later. Invokes build_orchestrator() during lifespan unless an orchestrator is injected (test mode).")
    Component(access_log, "AccessLogMiddleware", "Starlette middleware", "Emits structured access-log records. Reads/echoes X-Request-ID, sets the correlation_id ContextVar, and pushes the value into OTel baggage as 'mangomas.correlation_id'.")
    Component(correlation, "correlation.py", "ContextVar + logging filter", "ContextVar carrying the per-request correlation id; CorrelationFilter injects it into every log record.")
    Component(trace_mw, "TraceMiddleware", "Starlette middleware / OTel", "Opens and closes an OpenTelemetry span per request. Tracer is obtained lazily via trace.get_tracer() to avoid capturing NoopTracer at import time.")
    Component(error_handler, "MangomasError handler", "FastAPI exception handler", "Walks the exception MRO to select the most specific HTTP status code and returns a structured JSON envelope.")
    Component(health_routes, "Health routes", "FastAPI routes", "GET /healthz (+ /health alias) → liveness. GET /readyz (+ /ready alias) → ReadinessReport from check_ready(). Never authenticated.")
    Component(agent_routes, "Agent + history routes", "FastAPI routes", "GET /agents; POST /agents/{name}/invoke (emits invocation/error/duration metrics); POST /agents/{name}/stream; GET /history (bounded limit). Delegates to Orchestrator.")
    Component(workflow_routes, "Workflow routes (opt-in)", "FastAPI routes", "POST /workflows/run|validate. Resolve the graph source via the shared resolve_workflow_source, then load_workflow + execute_workflow. Reuse the MangomasError envelope (ConfigError 400 / AgentNotFound 404 / MaxStepsExceeded 422).")
    Component(backpressure_mw, "Backpressure + CORS + auth (opt-in)", "Middleware / dependency", "Default-OFF: MaxBodySizeMiddleware (413), ConcurrencyLimitMiddleware (503, reject-don't-queue), env-driven CORSMiddleware, and require_auth (bearer / X-API-Key via SecretsProvider, fail-closed). Backpressure sits inner of log/trace so rejections are still logged.")
    Component(tenancy_mw, "TenancyMiddleware (opt-in)", "Starlette middleware", "ADR-0017. Reads MANGOMAS_TENANCY__HEADER (default X-Tenant-ID) into the tenancy ContextVar; the storage adapters filter rows on it, so no route signature changes. Not installed when MANGOMAS_TENANCY__ENABLED=false, in which case every row uses the implicit 'default' tenant. Header values pass through the shared sanitize_header_token, the same log-injection / SQL-parameter defence the correlation header uses.")
  }

  Container_Boundary(telemetry_boundary, "Telemetry (src/mangomas/telemetry/ + metrics.py)") {
    Component(telemetry_bootstrap, "configure_telemetry()", "Idempotent bootstrap", "Installs the log handler (text | json), the TraceContext + Correlation filters, the W3C propagator and a TracerProvider. Idempotent by a process-global latch, so the FIRST caller wins — which is why no module may bind mangomas.telemetry.get_tracer at import time (spec-0023 R1a). Exporter selected by MANGOMAS_TELEMETRY__EXPORTER: console or gcp Cloud Trace (ADR-0009).")
    Component(meters, "configure_metrics() + record_* helpers", "OTel MeterProvider (opt-in)", "ADR-0013. Default-OFF behind MANGOMAS_TELEMETRY__METRICS_ENABLED. Emits agent invocation, error and duration instruments from the invoke route; metrics.py holds the lazily-bound record helpers so an un-configured process pays nothing.")
    Component(scoped_tracer, "build_scoped_tracer()", "Dedicated TracerProvider", "Routes harness spans to their own exporter when MANGOMAS_HARNESS__METRICS_EXPORTER != inherit. Cached by (namespace, exporter) so repeated build_orchestrator calls reuse one provider rather than leaking a SpanProcessor each time.")
  }

  Container_Boundary(eval_boundary, "Evaluation harness (src/mangomas/eval/) — opt-in") {
    Component(eval_runner, "EvalRunner", "Domain service", "Runs each dataset row through a Target, scores it with a Scorer, and aggregates an EvalReport. mean_score averages non-errored rows only; pass_rate keeps errored rows in the denominator. Bounded concurrency via MANGOMAS_EVAL__PARALLELISM.")
    Component(eval_registries, "Four registries", "Registry[T]", "scorer_registry, target_registry, sink_registry, dataset_source_registry — the same Registry[T] the agent/LLM/storage seams use. Built-ins self-register at import; entry-point plugins load only when MANGOMAS_DISCOVERY_ENABLED=true.")
    Component(eval_gate, "evaluate_gate + evaluate_regression_gate", "Pure functions", "Threshold verdict over an EvalReport, and a regression verdict over a diff against a baseline report. Both pure; the CLI maps a failing verdict to exit code 3 (distinct from 1=runtime, 2=config) and only after sinks have emitted.")
    Component(eval_sinks, "Sinks", "Sink impls", "console, json_file, sqlite_results, webhook, and the optional langfuse sink. Composed under per-sink fault isolation, so one failing sink cannot lose the others' output.")
  }

  Container_Boundary(harness_boundary, "Harness governance (src/mangomas/harness/ + scripts/) — opt-in") {
    Component(governance, "governance.py", "Policy module", "PROTECTED_PATHS and the BREAKING-CHANGE marker aliases, read from pyproject.toml's [tool.mangomas.governance] table — one source of truth shared with both scripts below.")
    Component(config_audit, "config_audit.py", "Decision table", "Evaluates a ConfigChange hook event against MANGOMAS_HARNESS__CONFIG_AUDIT_MODE (off | audit | block) for edits to .claude/settings*.json.")
    Component(protected_paths_gate, "check_protected_paths.py", "CI gate — authoritative", "Reads git diff/log between the PR base and head, state an in-session agent cannot rewrite. This is the real enforcement of the BREAKING-CHANGE trailer.")
    Component(frontmatter_lint, "lint_agent_frontmatter.py", "Linter + hook modes", "Pydantic-validated lint of .claude/agents and .claude/skills frontmatter, plus stdlib-only PreToolUse (advisory 'ask' on a protected-path edit) and PostToolUse (--emit-path for ruff) hook modes. The hook is ADVISORY only: Bash and MCP filesystem calls bypass its matcher entirely, which is why the CI gate above is the authoritative one (ADR-0021).")
  }

  Container_Boundary(secrets_boundary, "Secrets (src/mangomas/secrets/)") {
    Component(secrets_provider, "SecretsProvider", "Protocol", "get(name) -> str | None. Resolves a secret reference at orchestrator-build time. Cloud backends plug in via secrets_registry.")
    Component(env_secrets, "EnvSecretsProvider", "SecretsProvider impl", "Reads secrets from os.environ. Used when LLMSettings.secret_ref is set.")
    Component(gcp_secrets, "GCPSecretManagerProvider", "SecretsProvider impl", "Resolves secrets via google-cloud-secret-manager + ADC. Fails soft to None by default (ADR-002); with MANGOMAS_SECRETS__STRICT=true it raises SecretsResolutionError instead, mapped to 503 — so a broken secret backend cannot masquerade as an unset secret (spec-0003). Activated by MANGOMAS_SECRETS__PROVIDER=gcp.")
  }

  Container_Boundary(core_boundary, "Core (src/mangomas/core/)") {
    Component(orchestrator, "Orchestrator", "Domain service", "dispatch() and stream_dispatch() look up agents in a Registry[Agent], invoke handle() or stream(), and persist turns. Lazy OTel tracer.")
    Component(harness_orch, "_HarnessOrchestrator (opt-in)", "Orchestrator subclass", "Engaged only when MANGOMAS_HARNESS__ENABLED=true. Wraps dispatch + stream_dispatch in a harness.agent_invoke parent span carrying agent.name, harness.topology, messages.count attributes. Delegates the actual work to Orchestrator via super(). dispatch_pipeline / dispatch_fan_out inherit the wrap because they delegate through dispatch.")
    Component(registry, "Registry[T]", "Generic registry", "Thread-safe name → factory/instance store. Used for agents, LLM providers, and storage providers.")
    Component(health_svc, "check_ready()", "Health service", "Pings LLM via PingableLLMClient.ping() and checks DB connectivity. Returns ReadinessReport.")
  }

  Container_Boundary(agents_boundary, "Agents (src/mangomas/agents/)") {
    Component(chat_agent, "ChatAgent", "Agent + StreamingAgent", "Single-turn conversational agent. Falls back to buffered complete() with a warning log when the LLM does not implement StreamingLLMClient.")
    Component(summarize_agent, "SummarizeAgent", "Agent", "Fetches recent turns from TurnRepository and asks the LLM to summarise them.")
    Component(tool_agent, "ToolAgent", "Agent", "Multi-step control loop: calls LLM, parses tool-call fences, dispatches ToolSpec, repeats up to max_steps.")
    Component(planner_agent, "PlannerAgent", "Agent", "Generates a structured plan from user input.")
    Component(reviewer_agent, "ReviewerAgent", "Agent", "Reviews a plan or response and returns structured feedback.")
  }

  Container_Boundary(adapters_boundary, "Adapters (src/mangomas/adapters/)") {
    Component(llm_client, "LLMClient (resolved by llm_registry)", "Protocol — at runtime LMStudioClient or VertexClient", "Provider selected by MANGOMAS_LLM__PROVIDER. Both implementations satisfy LLMClient, StreamingLLMClient, and PingableLLMClient. Vertex requires the `mangomas[vertex]` optional extra; the SDK is lazy-imported so importing the module is always safe.")
    Component(sqlite_repo, "SQLiteRepository", "TurnRepository", "Persists conversation turns to a local SQLite database.")
    Component(postgres_repo, "PostgresRepository", "TurnRepository + AsyncCloseableRepository", "Persists conversation turns to Postgres via asyncpg with a connection pool. Activated by MANGOMAS_DB__PROVIDER=postgres; requires the `mangomas[postgres]` optional extra.")
    Component(embedding_client, "EmbeddingClient (resolved by embedding_registry)", "Protocol — LMStudioEmbeddingClient | SentenceTransformersEmbeddingClient | VertexEmbeddingClient", "Attached to ctx.embeddings when MANGOMAS_EMBEDDINGS__ENABLED=true. embed()/embed_batch()/aclose(). lmstudio uses httpx POST /v1/embeddings; sentence_transformers runs encode() in asyncio.to_thread (mangomas[embeddings-local]); vertex uses text-embedding-004 via ADC (mangomas[vertex]). Heavy SDKs lazy-imported.")
    Component(vector_store, "ChromaVectorStore (resolved by vector_registry)", "VectorStoreRepository", "Attached to ctx.vector_store when MANGOMAS_VECTOR__ENABLED=true. upsert/query/delete_by_source/aclose over a persistent Chroma collection created with hnsw:space=cosine; VectorMatch.score = 1 - distance/2. chromadb lazy-imported (mangomas[rag]).")
  }

  Container_Boundary(workflow_boundary, "Workflow (src/mangomas/workflow/) — opt-in") {
    Component(workflow_graph, "WorkflowGraph", "Frozen Pydantic model", "Discriminated union of agent / sequence / fan_out / loop / branch nodes forming a bounded tree. Loaded from a JSON path or inline definition by load_workflow (ConfigError boundary).")
    Component(execute_workflow, "execute_workflow()", "Driver", "Resolves the root node to a NodeExecutor and runs it against the Orchestrator. Every leaf is exactly one public dispatch call — the layer compiles to the imperative primitives rather than reimplementing them.")
    Component(node_registry, "node_registry + make_node_factory", "Registry[NodeExecutorFactory]", "Maps a node kind to its executor factory. Built-in nodes self-register at import; make_node_factory (nodes/_factory.py) builds each factory with a shared isinstance guard raising ConfigError, naming the closure _<kind>_factory for traceable failures.")
    Component(node_executors, "Node executors", "NodeExecutor impls", "agent → dispatch; sequence → threads output into the next input (equals dispatch_pipeline when all-agent); fan_out → dispatch_fan_out, or asyncio.gather for composite branches; loop → dispatch with a compiled acceptance predicate; branch → predicate-routed selection.")
  }

  Container_Boundary(rag_boundary, "RAG (src/mangomas/rag/) — opt-in") {
    Component(retrieval_tool, "RetrievalTool", "Tool", "name='retrieve'. Registered into a ToolRegistry and set on ctx.tools only when BOTH ctx.embeddings and ctx.vector_store are present, so ToolAgent auto-discovers it. execute() returns formatted top-k context.")
    Component(retriever, "Retriever", "Domain service", "search(query): embed query → vector_store.query → map VectorMatch → SearchResult. top_k from MANGOMAS_VECTOR__TOP_K.")
    Component(ingestion, "IngestionPipeline", "Domain service", "ingest(path): load → delete_by_source (idempotent re-ingest) → chunk_text → embed_batch in batch_size slices → upsert with stable {source}#{index} ids. CLI-only (mangomas rag ingest).")
  }

  Container_Boundary(cognitive_boundary, "Cognitive producer (src/mangomas/cognitive/) — opt-in") {
    Component(cognitive, "CognitiveSignalSink + producer", "Protocol + emit helper", "JSONL (default) or optional HTTP POST of CognitiveSignal 1.1.0. Wired on ctx.extras['cognitive_sink'] when MANGOMAS_SIGNAL__ENABLED=true. planner emits planning.proposal; reviewer emits review.finding. Failures log+swallow. tool has no harness role (map raises; emit skips); chat/summarize are unmapped observation and do not emit; retrieve stays local RAG.")
  }

  Rel(app_factory, access_log, "adds middleware")
  Rel(access_log, correlation, "set_correlation_id() / OTel baggage")
  Rel(app_factory, trace_mw, "adds middleware")
  Rel(app_factory, error_handler, "registers exception handler")
  Rel(app_factory, health_routes, "mounts routes")
  Rel(app_factory, agent_routes, "mounts routes")
  Rel(app_factory, workflow_routes, "mounts routes (opt-in)")
  Rel(app_factory, tenancy_mw, "adds middleware (opt-in, ADR-0017)")
  Rel(app_factory, telemetry_bootstrap, "lifespan calls configure_telemetry() with the configured exporter + log format")
  Rel(trace_mw, telemetry_bootstrap, "spans resolve against the TracerProvider it installed")
  Rel(agent_routes, meters, "record_agent_invocation / _error / _duration (opt-in)")
  Rel(harness_orch, scoped_tracer, "harness.agent_invoke spans (dedicated exporter when != inherit)")
  Rel(eval_runner, eval_registries, "resolves target / scorer / sink / dataset source by name")
  Rel(eval_runner, eval_gate, "report → GateResult; CLI exit 3 on failure")
  Rel(eval_runner, eval_sinks, "emits the report to every configured sink")
  Rel(eval_runner, orchestrator, "the agent / pipeline / fan_out targets dispatch through the public surface")
  Rel(frontmatter_lint, governance, "reads PROTECTED_PATHS + marker aliases")
  Rel(protected_paths_gate, governance, "reads PROTECTED_PATHS + marker aliases")
  Rel(app_factory, backpressure_mw, "installs when configured")
  Rel(agent_routes, orchestrator, "dispatch() / stream_dispatch() (or _HarnessOrchestrator when harness.enabled=true)")
  Rel(workflow_routes, execute_workflow, "load_workflow() then execute_workflow()")
  Rel(execute_workflow, node_registry, "resolve_executor(node) by kind")
  Rel(node_registry, node_executors, "builds via make_node_factory")
  Rel(execute_workflow, workflow_graph, "walks the frozen node tree")
  Rel(node_executors, orchestrator, "dispatch / dispatch_pipeline / dispatch_fan_out")
  Rel(backpressure_mw, secrets_provider, "resolve expected auth token")
  Rel(harness_orch, orchestrator, "delegates via super() — harness only adds the parent span")
  Rel(health_routes, health_svc, "check_ready(orchestrator)")
  Rel(health_svc, llm_client, "ping()")
  Rel(orchestrator, registry, "looks up Agent by name")
  Rel(orchestrator, sqlite_repo, "persists turn via TurnRepository (when provider=sqlite)")
  Rel(orchestrator, postgres_repo, "persists turn via TurnRepository (when provider=postgres)")
  Rel(registry, chat_agent, "resolves 'chat'")
  Rel(registry, summarize_agent, "resolves 'summarize'")
  Rel(registry, tool_agent, "resolves 'tool'")
  Rel(registry, planner_agent, "resolves 'planner'")
  Rel(registry, reviewer_agent, "resolves 'reviewer'")
  Rel(chat_agent, llm_client, "complete() / stream()")
  Rel(summarize_agent, llm_client, "complete()")
  Rel(tool_agent, llm_client, "complete()")
  Rel(planner_agent, llm_client, "complete() / stream()")
  Rel(reviewer_agent, llm_client, "complete() / stream()")
  Rel(app_factory, secrets_provider, "resolves api_key / credentials_json via LLMSettings.secret_ref")
  Rel(secrets_provider, env_secrets, "default impl (provider='env')")
  Rel(secrets_provider, gcp_secrets, "cloud impl (provider='gcp')")
  Rel(tool_agent, retrieval_tool, "execute('retrieve') when RAG enabled (resolved from ctx.tools)")
  Rel(retrieval_tool, retriever, "search(query)")
  Rel(retriever, embedding_client, "embed(query)")
  Rel(retriever, vector_store, "query(embedding, top_k)")
  Rel(planner_agent, cognitive, "planning.proposal after handle (contained)")
  Rel(reviewer_agent, cognitive, "review.finding after handle (contained)")
  Rel(ingestion, embedding_client, "embed_batch(chunks)")
  Rel(ingestion, vector_store, "delete_by_source() then upsert()")
```

## Notes

- `Registry[T]` is the single extensibility point for agents and providers.
  Adding a new agent or LLM adapter does not require changes to `Orchestrator`,
  `create_app()`, or any existing component.
- The **LLM adapter** shown above is the one resolved at runtime by
  `llm_registry`: today either `LMStudioClient` (default, `MANGOMAS_LLM__PROVIDER=lmstudio`)
  or `VertexClient` (`MANGOMAS_LLM__PROVIDER=vertex`, requires the
  `mangomas[vertex]` optional extra). Vertex's SDK is lazy-imported inside
  `VertexClient.__init__`, so simply importing `mangomas.adapters.llm.vertex`
  never triggers a hard dependency.
- `TraceMiddleware` and `AccessLogMiddleware` are the two middleware components.
  `AccessLogMiddleware` owns the **correlation id** lifecycle: it reads/echoes
  `X-Request-ID`, sets the `correlation_id` ContextVar, and pushes the value into
  OTel baggage as `mangomas.correlation_id`. See
  [observability.md](observability.md) for the full request lifecycle.
- The **SecretsProvider seam** (`src/mangomas/secrets/`) is consulted at
  orchestrator-build time when `LLMSettings.secret_ref` is set. For LM Studio
  the resolved value becomes `api_key`; for Vertex it becomes the
  service-account JSON body (`credentials_json`). The GCP Secret Manager
  backend shipped in v0.3.1 and is E2E verified; additional cloud backends
  (e.g. Vault) plug in via `secrets_registry.register()` with no changes
  to agents or adapters.
- `check_ready()` is in `src/mangomas/api/health.py` and is called by the
  `/readyz` route handler. Both `LMStudioClient.ping()` and
  `VertexClient.ping()` are translated by `check_ready()` into a uniform
  `ReadinessReport`.
- The **evaluation harness** is not shown here because it does not run inside
  the FastAPI Application container — it is a CLI consumer of the same
  composition root and uses the same `Orchestrator`. See
  [docs/eval/harness.md](../eval/harness.md) for its component layout.
- The **Claude Code harness** (`_HarnessOrchestrator`, the frontmatter
  linter, the SessionStart hook) is opt-in via
  `MANGOMAS_HARNESS__ENABLED`. When disabled (the default),
  `build_orchestrator` returns a plain `Orchestrator` and the harness
  components above are not engaged. When enabled, the wrapper is
  transparent — every existing `dispatch` / `stream_dispatch` call works
  unchanged; the only observable difference is a `harness.agent_invoke`
  parent span and three extra debug-level log lines per invocation.
- The **RAG seam** (`src/mangomas/adapters/embeddings/`,
  `src/mangomas/adapters/vector/`, `src/mangomas/rag/`) follows the same
  protocol-first discipline as the LLM/storage seams. `EmbeddingClient` and
  `VectorStoreRepository` are `@runtime_checkable` Protocols resolved by
  `embedding_registry` / `vector_registry` in `composition.py`. The `rag/`
  package imports only those protocol surfaces (plus its own `models` and
  `core`), so the vector layer never imports `rag/` — no cycle. The
  `RetrievalTool` is attached to `ctx.tools` only when both seams are
  enabled, so `ToolAgent` needs no new plumbing. `IngestionPipeline` runs
  only from the CLI (`mangomas rag ingest`) and is not part of the request
  path. `Orchestrator.aclose()` closes `ctx.embeddings` and
  `ctx.vector_store` (fault-tolerant, idempotent) so neither leaks per run.
- The **cognitive producer** (`src/mangomas/cognitive/`) is opt-in via
  `MANGOMAS_SIGNAL__ENABLED`. It imports `mango_contracts` in-process and
  writes JSONL (optional HTTP). The sibling Code Agent Harness is the C1
  consumer of that schema; this container does not broker execution.
  Streaming does not emit. Failures log+swallow.
- All components that accept external input are configurable via
  `mangomas.config.Settings`; no hardcoded endpoints or model ids.
