# ADR-0003: Adopt eval-harness patterns natively; Langfuse as an optional sink

## Status

Accepted

## Context

An external framework (`ianshank/Agents`) is a Langfuse-integrated LLM evaluation
harness whose value is a pattern vocabulary — Scorers, Datasets, Targets, Sinks,
Gating, and plugin entry-points. Mango-Mas already has most of these natively
under `src/mangomas/eval/` (Scorer protocol + registry, `load_jsonl` datasets,
the `Orchestrator`+`agent_name` target seam, and `EvalRunner`/`EvalReport`). The
genuine gaps are CI gating, a sink abstraction, more pure-Python scorers, plugin
discovery, and a config version marker. The decision is how to close them without
violating local-first / protocol-first / optional-extra discipline.

## Decision

Port the missing eval-harness *patterns* natively into `src/mangomas/eval/`,
satisfying the project's existing protocols and `Registry[T]`. Integrate Langfuse
**only** as one optional `Sink` behind a new `mangomas[langfuse]` extra
(lazy-imported, env/ADC credentials), never as a core dependency.

## Consequences

### Positive

- No new mandatory runtime dependencies; everything stays opt-in and default-OFF.
- Reuses `Registry[T]`, `get_tracer`, structured logging, and `ConfigError`, so
  the new surface matches the rest of the codebase.
- CI can gate on quality regressions via a dedicated exit code (3).

### Negative / Trade-offs

- We maintain our own thin scorers/sinks rather than importing a ready-made
  harness, so feature parity with the upstream project is a deliberate, ongoing
  choice rather than automatic.

### Neutral

- Langfuse SDK is pinned `>=2,<3` to avoid the breaking v3 API rewrite.
- A per-section `schema_version` on `EvalSettings` is the forward-compat
  mechanism for future eval-config evolution.

## Alternatives Considered

- **Vendor the framework as a dependency** — rejected: pulls Langfuse and its
  transitive deps into core, violating the local-first / optional-extra rule.
- **Fork & subclass the framework's abstractions** — rejected: its Protocols do
  not match `ScorerContext` / `EvalReport` and would duplicate `Registry[T]`.

## References

- Code: `src/mangomas/eval/gate.py`, `src/mangomas/eval/sink.py`,
  `src/mangomas/eval/sink_registry.py`, `src/mangomas/eval/sinks/`,
  `src/mangomas/eval/discovery.py`, `src/mangomas/eval/scorers/`
- Related ADRs: ADR-0001, ADR-0002
