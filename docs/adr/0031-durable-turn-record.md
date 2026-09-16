# ADR-0031: The turn record is versioned, and failures are recorded

- **Status:** Accepted
- **Date:** 2026-09-16
- **Spec:** spec-0033 — **not yet written.** This repo's convention is
  spec-before-code for a non-trivial feature, and this ADR records a
  persistence-contract change that landed ahead of it. Flagged rather than
  glossed: the requirements and acceptance criteria owe a spec, and the
  remaining PR B milestones should not start until it exists.
- **Source:** `docs/analysis/20260916-workflow-governance-audit.md` §1, §6, §7

## Context

`Orchestrator.dispatch` reached `ctx.repo.save_turn` only after its loop
returned normally. Every failure path — `MaxStepsExceeded`, `StepTimeout`,
`ToolExecutionError`, any LLM error — propagated past that call and wrote
nothing.

The consequence is the one shape an audit trail must not have: the only durable
log of the system's behaviour recorded **successes and nothing else**, so "no
record" and "never happened" were indistinguishable. A tool that had already
mutated external state before a later step timed out left no trace at all.

The record itself was also unversioned — `(id, ts, agent, request, response,
tenant)` — so no consumer could tell a row written by this build from one
written before a column it depends on existed, and `_ensure_tenant_column` was
a hand-rolled, single-purpose migration rather than a contract.

## Decision

**1. One record definition, shared by every backend.**
`adapters/storage/_schema.py` owns `TURN_RECORD_COLUMNS`, `TURN_SELECT_COLUMNS`,
`TurnStatus` and `TURN_SCHEMA_VERSION`. Both adapters generate their DDL,
their `SELECT` list and their row→dict mapping from it. The alternative — each
adapter spelling its own columns — is how a SQLite row and a Postgres row come
to mean subtly different things, which is precisely the drift this repo's
governance audit found between two hand-copied modules.

**2. Rows carry `schema_version`, `status`, `error_code` and `error`.**
Pre-existing rows default to `status='ok'`, which is what they were: before the
column existed a row was only ever written on success.

**3. Migrations are additive, generated, and never destructive.** A column is
appended when absent and never dropped, altered or reordered, so a database
written by an older build keeps working. This generalises the contract `tenant`
already shipped under (ADR-0017) rather than adding a third hand-rolled copy.

**4. `FailureRecordingRepository` is a strict Protocol extension.** Callers
probe `hasattr(repo, "save_failed_turn")` exactly as they already do for
`AsyncCloseableRepository`, so a third-party backend written against the bare
`TurnRepository` keeps satisfying its protocol unchanged.

**5. The recording wrap lives in `composition/`, not `core/`.**
`core/orchestrator.py` is a protected path. `_FailureRecordingMixin` sits ahead
of both concrete orchestrators in the MRO; `_HarnessOrchestrator.dispatch`
delegates through `super().dispatch(...)`, so the harness-enabled and
harness-disabled builds are both wrapped without editing the contract. Same
seam ADR-0028 chose for per-agent LLM overrides.

**6. A routing error is not a turn, and origin is decided by position.** When
the orchestrator's own lookup raises `AgentNotFound`, no agent was invoked, so
nothing executed and no side effect was possible; the honest answer to "what
did this system do?" is "nothing". Recording it would also hand any caller a
cheap write-amplification vector against the turn store — request agents that
do not exist, inflate a table whose whole value is that it describes real work.

What the mixin must *not* do is infer that from the exception's class. A
registered agent is free to raise `AgentNotFound` from inside its own `handle`
— one that dispatches onward, or routes a tool by name — and that one
executed: the request reached user code and a tool may already have changed
something outside this process. Both failures arrive at the same `except` as
the identical class, so the mixin settles routability *before* it dispatches
(`agent_name in self.list_agents()`) and uses that, never the type. The
`max_steps` argument check is excluded the same way, by being performed outside
the recording `try` rather than by matching `ValueError` — which would swallow
a `ValueError` raised from inside `handle`, an execution failure that must be
recorded.

**7. Recording is best-effort and never masks the original error.** A broken
repository is logged and swallowed *inside* `_record_failure`; the caller's
exception is re-raised untouched. Losing the real failure to a secondary
persistence failure — while recording a failure — is a trade worth refusing.

## Consequences

**What this buys.** A failed run is now visible in the same place a successful
one is, with a typed `error_code` that groups without parsing prose. Adding a
column reaches both backends' DDL, reads and row mapping at once. The row→dict
mapper zips *strictly* against the shared column tuple, so a SELECT that falls
out of step fails loudly at the first read instead of silently dropping a field.

**What it does not buy.** This records that a dispatch failed; it does not make
the failure *recoverable*. There is still no step-level state, so a workflow
that dies part-way cannot be resumed — that is the deferred run-ledger work,
which has **no ADR yet** — it needs one before any code, and the number is not
allocated here so this reference cannot point at the wrong decision. It also does not yet carry identity: `run_id`, `task_id`,
`trace_id`, `model` and `actor` are the next milestone, and until they land the
turn record still cannot be joined to a `CognitiveSignal` or answer "which
model produced this?".

**Cost.** Every write now carries four more columns, and an existing database
takes four `ALTER TABLE` statements on first open after upgrade. Both are
one-off and bounded.

## Alternatives considered

- **Edit `core/orchestrator.py` directly.** Rejected: it is a protected path,
  and the composition seam already exists precisely so a cross-cutting concern
  does not have to touch the contract.
- **A single JSON `metadata` column instead of explicit columns.** One
  migration instead of several and extensible without further DDL, but it makes
  `status` and the future `run_id` unqueryable. An audit record that cannot
  answer "show me the failures" with a `WHERE` clause is a worse record.
- **Recording every failure, routing errors included.** How this first
  shipped, and it was wrong in both directions: it logged turns that never
  happened and it let an unauthenticated-shaped request write rows. Caught by
  `tests/integration/`, which CI runs via `make gated-suites` and `make gate`
  does not — the local gate was green when the defect went out.

- **A tolerant row mapper (`row.get(name)`).** Rejected: it would have made
  this change land without touching the Postgres test fixtures, which is
  exactly the point — tolerating a missing column is how a backend quietly
  stops returning one. The fixtures are now generated from the shared tuple
  instead.
