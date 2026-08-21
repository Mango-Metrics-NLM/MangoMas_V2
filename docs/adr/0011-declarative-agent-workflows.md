# ADR-0011: Declarative multi-agent workflow graphs

## Status

Accepted

## Context

Multi-agent topologies were only expressible imperatively in Python
(`dispatch_pipeline`, `dispatch_fan_out`). The roadmap (spec 0005) requires
composing planner → executor → reviewer graphs declaratively (JSON/config) and
executing them via the `Orchestrator`, while keeping single-agent dispatch and
the imperative topology methods byte-identical and default-OFF.

## Decision

Add a pure-domain `workflow/` package (sibling of `rag/`/`eval/`) that compiles a
frozen-Pydantic `WorkflowGraph` — a bounded tree of `agent` / `fan_out` / `loop`
nodes composed inside a `sequence` — down to the existing public `Orchestrator`
dispatch methods, resolved per node via a `node_registry`. Executors are
metadata-transparent, so an all-agent `sequence` equals `dispatch_pipeline`.

## Consequences

### Positive

- Zero edits to protected core files; `errors.py` reused (`ConfigError` /
  `AgentNotFound`); `build_orchestrator` unchanged.
- Every leaf is a 1:1 public dispatch call — no reimplemented pipeline threading,
  fan-out `gather`, or acceptance loop; harness/persistence/tracing inherited.
- Acyclic by construction (bounded tree) → no cycle detection.

### Negative / Trade-offs

- Bounded nesting: `fan_out` branches and `loop` bodies are single agents in v1;
  composite bodies and conditional branching are deferred.
- The closed discriminated union means new node kinds edit the union *and* a node
  module — the `node_registry` alone does not make kinds pluggable yet.

### Neutral

- Graph source is `WorkflowSettings`-driven (`MANGOMAS_WORKFLOW__*`), default-OFF.
- The graph declares its own `schema_version`, validated at load.

## Alternatives Considered

- **Explicit-edge DAG** — rejected: needs topological sort + cycle detection for
  no v1 benefit (no conditional edges yet).
- **Reuse the eval `target` config** — rejected: targets return `str` and do not
  compose (nodes must thread `AgentResponse`).
- **`Orchestrator.dispatch_graph` method** — rejected: edits the protected
  `orchestrator.py` and couples the graph engine to core.
- **Exhaustive `match` instead of `node_registry`** — rejected: breaks the repo's
  registry idiom (~10 sibling registries) and the future discovery seam.
- **Open node model + entry-point discovery** (`mangomas.workflows`) — deferred:
  requires migrating the closed union to an open model first.

## References

- Code: `src/mangomas/workflow/` (`graph.py`, `predicate.py`, `registry.py`,
  `executor.py`, `loader.py`, `nodes/`), `src/mangomas/config/workflow.py::WorkflowSettings`,
  `src/mangomas/cli/commands/workflow.py` (`workflow` sub-app).
- Related: spec `specs/0005-declarative-agent-workflows.md`; ADR-0004
  (eval target/source indirection — the pattern mirrored here).
