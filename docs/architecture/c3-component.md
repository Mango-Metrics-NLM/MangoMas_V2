# C3 — Component Diagram (FastAPI Application)

This diagram shows the components inside the FastAPI Application container and
how they interact at the class / module level.

```mermaid
C4Component
  title Mango-Mas V2 — FastAPI Application Components

  Container_Boundary(api_boundary, "FastAPI Application (src/mangomas/api/)") {
    Component(app_factory, "create_app()", "FastAPI factory function", "Constructs and configures the FastAPI app. Wires middleware, exception handlers, and routes. Invokes build_orchestrator() during lifespan unless an orchestrator is injected (test mode).")
    Component(access_log, "AccessLogMiddleware", "Starlette middleware", "Emits structured access-log records. Reads/echoes X-Request-ID, sets the correlation_id ContextVar, and pushes the value into OTel baggage as 'mangomas.correlation_id'.")
    Component(correlation, "correlation.py", "ContextVar + logging filter", "ContextVar carrying the per-request correlation id; CorrelationFilter injects it into every log record.")
    Component(trace_mw, "TraceMiddleware", "Starlette middleware / OTel", "Opens and closes an OpenTelemetry span per request. Tracer is obtained lazily via trace.get_tracer() to avoid capturing NoopTracer at import time.")
    Component(error_handler, "MangomasError handler", "FastAPI exception handler", "Walks the exception MRO to select the most specific HTTP status code and returns a structured JSON envelope.")
    Component(health_routes, "Health routes", "FastAPI routes", "GET /healthz (+ /health alias) → liveness. GET /readyz (+ /ready alias) → ReadinessReport from check_ready().")
    Component(agent_routes, "Agent routes", "FastAPI routes", "GET /agents, POST /agents/{name}/invoke, POST /agents/{name}/stream. Delegates to Orchestrator.")
  }

  Container_Boundary(secrets_boundary, "Secrets (src/mangomas/secrets/)") {
    Component(secrets_provider, "SecretsProvider", "Protocol", "get(name) -> str | None. Resolves a secret reference at orchestrator-build time. Cloud backends plug in via secrets_registry.")
    Component(env_secrets, "EnvSecretsProvider", "SecretsProvider impl", "Reads secrets from os.environ. Used when LLMSettings.secret_ref is set.")
    Component(gcp_secrets, "GCPSecretManagerProvider", "SecretsProvider impl", "Resolves secrets via google-cloud-secret-manager + ADC. Collapses all failure modes into None per ADR-002. Activated by MANGOMAS_SECRETS__PROVIDER=gcp.")
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
  }

  Rel(app_factory, access_log, "adds middleware")
  Rel(access_log, correlation, "set_correlation_id() / OTel baggage")
  Rel(app_factory, trace_mw, "adds middleware")
  Rel(app_factory, error_handler, "registers exception handler")
  Rel(app_factory, health_routes, "mounts routes")
  Rel(app_factory, agent_routes, "mounts routes")
  Rel(agent_routes, orchestrator, "dispatch() / stream_dispatch() (or _HarnessOrchestrator when harness.enabled=true)")
  Rel(harness_orch, orchestrator, "delegates via super() — harness only adds the parent span")
  Rel(health_routes, health_svc, "check_ready(orchestrator)")
  Rel(health_svc, llm_client, "ping()")
  Rel(orchestrator, registry, "looks up Agent by name")
  Rel(orchestrator, sqlite_repo, "persists turn via TurnRepository (when provider=sqlite)")
  Rel(orchestrator, postgres_repo, "persists turn via TurnRepository (when provider=postgres)")
  Rel(registry, chat_agent, "resolves 'chat'")
  Rel(registry, summarize_agent, "resolves 'summarize'")
  Rel(registry, tool_agent, "resolves 'tool_agent'")
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
- All components that accept external input are configurable via
  `mangomas.config.Settings`; no hardcoded endpoints or model ids.
