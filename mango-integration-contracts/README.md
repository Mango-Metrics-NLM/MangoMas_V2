# mango-integration-contracts

Versioned, strict, **non-authoritative** envelope shared by Mango-Mas V2
(cognitive plane) and [Mango Code Agent
Harness](https://github.com/ianshank/Mango_Code_Agent-Harness) (execution /
authority plane).

Neither system should import the other's internals. Mango-Mas emits a
`CognitiveSignal`. The harness validates, archives, optionally converts it
into bounded prompt context, and independently decides whether any action is
permissible.

**Schema version:** `1.1.0` (breaking vs the harness's in-tree dataclass
`1.0.0` — companion bump required there).

## Install

```bash
pip install -e ./mango-integration-contracts
```

In this repository the package is also on pytest's `pythonpath`, matching
`eval_harness_bridge`. Isolated coverage: `make contracts-coverage` (100%).

## Invariant

Mango-Mas can emit `CognitiveSignal`. A signal can cause review, context
enrichment, archival, or a separate `ProposedAction` record to be created.
A signal alone can never cause a command, write, network mutation, approval,
completion, merge, release, capability grant, or policy change.

## Enforcement at ingest

1. Strict schema first (`extra="forbid"`, TTL skew ≤ 5s, payload registry).
2. `confidence` / `severity` / `recommendation` / `payload` are not PDP inputs.
3. Unknown `signal_type`s are archived, never executed.
4. Prompt eligibility requires the signal's `policy_snapshot_hash` to match
   the active run snapshot when the harness supplies one.
5. Expired signals are archived; they are not silently renewed.
6. Executable detail lives on `ProposedAction`, cited only by opaque refs.
7. `is_prompt_eligible` is a context-compiler predicate, not a permission API.
