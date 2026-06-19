# ADR-0004: Eval target (and source) indirection

## Status

Accepted

## Context

The eval harness (ADR-0003) hard-wired every row to a single registered agent:
`EvalRunner.run(dataset, agent_name)` called `orch.dispatch(agent_name, request)`
directly. That blocks evaluating a multi-agent pipeline / fan-out, or comparing
a run against a deterministic baseline (needed for the upcoming regression
gating). We want the same protocol-first / `Registry[T]` seam already used for
scorers and sinks, without breaking the existing `run` signature or
`EvalReport` contract.

## Decision

Introduce a `Target` protocol (`async run(self, request, *, orch) -> str`)
resolved by name through a `target_registry`, with built-ins `agent` (default),
`pipeline`, `fan_out`, and `echo`. `EvalRunner.run` keeps `agent_name` as a
positional and gains an optional keyword `target`; an explicit target wins,
otherwise `agent_name` is wrapped in the default `agent` target. `EvalReport`
gains an additive `target_name` field defaulting to `""`.

Symmetrically, introduce a `DatasetSource` protocol (`async load(self) ->
list[DatasetRow]`) resolved through a `dataset_source_registry`, with built-ins
`jsonl` (default — wraps the existing `load_jsonl`), `inline`, and the optional
`langfuse` source. The `--dataset` flag keeps working by feeding the `jsonl`
source's `path` option. Both seams reuse the same `Registry[T]` + entry-point
discovery machinery (groups `mangomas.eval.targets` /
`mangomas.eval.dataset_sources`).

## Consequences

### Positive

- Eval can target topologies and deterministic baselines; the `echo` target
  gives regression gating (ADR-0005, planned) a no-LLM reference.
- Reuses the existing `Registry[T]` + entry-point discovery machinery — a new
  `mangomas.eval.targets` group ships third-party targets with no core change.
- Fully backward compatible: legacy `run(dataset, agent_name=...)` callers and
  `EvalReport.agent_name` consumers are unchanged (for the `agent` target,
  `agent_name == target_name == <agent>`).

### Negative / Trade-offs

- `EvalReport.agent_name` is now the *target* name (e.g. `echo`, `pipeline`)
  when a non-agent target runs — a mild semantic widening of the field.

### Neutral

- Targets receive the orchestrator per-call (not at construction), mirroring how
  `Scorer.score` receives its `ScorerContext`; the registry factory only needs
  options.

## Alternatives Considered

- **Closure target (`Callable[[AgentRequest], Awaitable[str]]`)** — rejected: a
  named protocol object matches `Scorer`/`Sink`, is registry- and
  discovery-friendly, and carries a stable `name` for the report.
- **New `run_target()` method, leave `run()` alone** — rejected: duplicates the
  parallelism / fail-fast loop; the optional-keyword extension is lower-risk.
- **Add `target_name` by overloading `agent_name`** — rejected: an additive
  field keeps `asdict` round-trips and old baseline JSON valid.

## References

- Code: `src/mangomas/eval/target.py`, `eval/target_registry.py`,
  `eval/targets/`, `eval/runner.py` (`EvalRunner.run`, `_resolve_target`);
  `eval/dataset_source.py`, `eval/sources/`, `cli/main.py`
  (`_build_target`, `_build_dataset_source`).
- Related ADRs: ADR-0003 (eval harness adopt-vs-build).
