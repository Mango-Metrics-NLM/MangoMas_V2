# Governance hardening — delivery plan

- **Branch:** `claude/workflow-governance-audit-c6otlb` (plan only); one branch per PR block below
- **Date:** 2026-09-16
- **Target release:** rolling
- **Status:** In progress
- **Specs:** spec-0032 … spec-0035 — **none written yet.** Each PR block below
  names the spec it needs; the spec lands before that block's code, per
  `CLAUDE.md` § Spec-Driven Development.
- **ADRs written:** ADR-0030 (self-protecting governance gate), ADR-0031
  (durable turn record), ADR-0032 (signal expiry and replay resistance),
  ADR-0033 (boundary honesty).
- **ADR still owed:** the deferred **workflow run ledger** has no number
  allocated — it is *not* ADR-0032, which this work used for signal expiry.
- **Source:** `docs/analysis/20260916-workflow-governance-audit.md`

## Status — 2026-09-16

**Delivered:** PR A in full (A0–A5), PR B milestone B0 plus the versioned/typed
half of B1, PR C milestones C0–C2, PR D in full (D0–D4). `make gate` green at
every commit.

**Outstanding, and why:**

- **B1 (identity columns)** — `schema_version`, `status`, `error_code` and
  `error` landed; `run_id`, `task_id`, `trace_id`, `model` and `actor` did not.
  The record is versioned and records failures, but still cannot be *joined*
  to a `CognitiveSignal` or answer "which model produced this?".
- **B2 (thread `run_id`/`task_id` from the edge)** — not started; depends on
  the identity columns above.
- **B3 (actor)** — still blocked on the per-principal auth decision, as
  recorded below. D1 shipped the honest interim.
- **C3 (give `ProposedAction` a producer)** — deliberately not started. Its
  idempotency key derives from `run_id`/`task_id`, which are not real until
  B2. Wiring it now would mint stable-looking keys over per-call UUIDs, which
  is worse than leaving the primitive unused.

## Executive summary

The audit found nine gaps. They are not nine problems. **Four are one problem**
— there is no durable record of a run — and **four more are one problem** — the
gates that govern this repo read their own policy from the tree they are
judging. That shapes the order: PR A closes the self-amendment and
gate-subversion holes first, because every later control is only worth what the
gate protecting it is worth; PR B then adds the single missing abstraction (a
run/step record) that closes six of the nine audit sections at once. PR C wires
the replay and idempotency primitives that already exist in
`mango-integration-contracts` and have no producer or consumer. PR D closes the
honesty gaps — places where a name promises a control the code does not
implement.

The constraint that shaped this sequencing: **no milestone may edit a protected
path**, and every milestone starts with a test that is red before it and green
after (`docs/plans/_template.md`, spec-0022 R13). Where a milestone's guard is
an *exclusion* rule, it carries a two-sided mutation proof per the
`mango-mutation-proof` skill — the audit's own headline finding (N1 below) is a
one-sided guard that would pass while the thing it guards is disabled.

---

## PR A — Gate integrity (spec-0032)

The gates are the load-bearing controls. Until they cannot be weakened from
inside the tree they govern, every item in PR B–D is advisory.

`tests/test_check_coverage.py` is the model to copy, not invent: it already
proves its own gate is non-vacuous — every source path has a floor
(`test_every_top_level_source_path_has_a_floor`), no glob silently stops
matching (`test_directory_floors_use_a_recursive_glob`), no exclude pattern
swallows real code (`test_no_exclude_pattern_swallows_real_code`). Nothing does
this for the protected-path gate. PR A is that test file's discipline, applied
to governance.

### Milestone A0 — Protect the policy and the verifier ✅

- **Failing test first:** `tests/harness/test_governance.py` —
  `test_the_governance_surface_protects_itself`, parametrised over the
  self-governing set, asserting each is in `PROTECTED_PATHS`. Red today for all
  eight entries.
- **Depends on:** nothing — parallel-safe.
- Add to `pyproject.toml`'s `[tool.mangomas.governance] protected_paths`:
  `pyproject.toml`, `scripts/check_protected_paths.py`, `scripts/_governance.py`,
  `src/mangomas/harness/governance.py`, `.github/workflows/ci.yml`, `Makefile`,
  `sitecustomize.py`, `.mcp.json`.
- Update **both** fallback constants in the same commit
  (`src/mangomas/harness/governance.py:41-53` and the
  `scripts/lint_agent_frontmatter.py` copy) — the existing
  `test_package_fallback_matches_the_pyproject_table` /
  `test_scripts_fallback_matches_the_pyproject_table` pins will fail otherwise,
  which is those pins working.
- **Honest limit, state it in the spec:** this makes weakening the policy
  *require a `BREAKING-CHANGE` commit message*. It does not make the policy
  externally authoritative — a branch that carries the marker can still shrink
  the set. A1 is what closes that.

### Milestone A1 — Evaluate the policy against the base ref ✅

- **Failing test first:** `tests/test_check_protected_paths.py` —
  `test_gate_uses_the_base_refs_policy_not_the_heads`: build a two-commit
  fixture repo whose head removes `src/mangomas/errors.py` from
  `protected_paths` *and* edits that file, with no marker. Assert
  `EXIT_MISSING_MARKER`. Red today (the gate reads the head's table and passes).
- **Depends on:** A0 (keeps the two changes reviewable apart; A1 works without it).
- `scripts/check_protected_paths.py` reads the table from
  `git show <base-ref>:pyproject.toml` rather than the working tree. Fall back
  to the working tree only when the base read fails, printing a loud stderr
  line naming the fallback — never silently.
- Needs **ADR-0030**: this changes what the gate's input *is*, which is a
  boundary.

### Milestone A2 — Make the `sitecustomize` guard two-sided ✅

- **Failing test first:** in `tests/regression/test_origin_defects.py`, replace
  the `in` assertion with an exact-token assertion, then run the
  `mango-mutation-proof` loop: append `--cov-fail-under=0` to the value
  `sitecustomize.py` injects and confirm the test goes **red**. Today it stays
  green — that is the defect.
- **Depends on:** nothing — parallel-safe.
- `tests/regression/test_origin_defects.py:35` asserts
  `"-p no:randomly" in result.stdout`. Presence, not exclusivity. Any *added*
  token rides along, and `PYTEST_ADDOPTS` is applied by pytest, so a token like
  `--cov-fail-under=0` or `--deselect tests/harness` silently weakens the test
  and coverage gates for every invocation including CI's `make test`.
- Assert the injected value contains *only* the expected tokens.

### Milestone A3 — Refuse built-in override in eval plugin discovery ✅

- **Failing test first:** `tests/eval/test_discovery.py` —
  `test_plugin_cannot_override_a_builtin_scorer`: register an entry point named
  `exact_match` and assert `scorer_registry.get("exact_match")` is still the
  built-in. Red today (`eval/discovery.py:93-100` logs INFO and overrides).
- **Depends on:** nothing — parallel-safe.
- Mirror `agents/discovery.py:73-76`, which already refuses a built-in
  collision with a WARNING. The asymmetry is the finding: the side that permits
  override is the side whose registries feed the CI quality gate (exit 3), the
  regression baseline, and the cost gate.
- Keep override available behind an explicit opt-in
  (`MANGOMAS_DISCOVERY_ALLOW_BUILTIN_OVERRIDE`, default `false`) so the
  documented last-call-wins behaviour is not removed, only closed by default.

### Milestone A4 — Scope the `BREAKING-CHANGE` trailer to a path ✅

- **Failing test first:** `tests/test_check_protected_paths.py` —
  `test_marker_naming_one_file_does_not_approve_another`: two protected files
  changed, one marker naming only the first → `EXIT_MISSING_MARKER`. Red today
  (any marker approves every protected path in the range).
- **Depends on:** A1 (same file, same review).
- Accept `BREAKING-CHANGE: <path> — <rationale>`; require every protected path
  in the diff to be named by some marker in the range. Keep the bare form
  accepted for back-compat, but have it satisfy only paths no scoped marker
  names — so adopting the scoped form is not a cliff.
- Update `scripts/_governance.py` and its `harness/governance.py` mirror
  together; the fallback-drift pins enforce that they stay in step.

### Milestone A5 — Stop interpolating a model-chosen path into a shell ✅

- **Failing test first:** `tests/test_lint_agent_frontmatter.py` —
  `test_emit_path_rejects_shell_metacharacters`: a payload whose
  `tool_input.file_path` contains `"; id; "` must not be echoed verbatim. Red
  today (`_extract_tool_path`, `scripts/lint_agent_frontmatter.py:554-568`,
  returns the raw string).
- **Depends on:** nothing — parallel-safe.
- `.claude/settings.json`'s `PostToolUse` hook pipes that stdout through
  `xargs -r -I{} sh -c '… "{}" …'`. The `{}` lands inside a double-quoted shell
  word, so a path containing `"` escapes it. The path is chosen by the model,
  which can be steered by file content it read.
- Two changes, both cheap: reject shell metacharacters in `--emit-path` (the
  contracts package already does exactly this for a string it *never* executes
  — `proposed_action.py:51-56`), and drop the `sh -c` wrapper in favour of
  `xargs -r -0` with a NUL-delimited path.

---

## PR B — The run record (spec-0033)

**This is the highest-value block in the plan.** Audit sections 1, 2, 6, 7, 8
and 9 all reduce to the same missing thing: nothing durable describes a run.
`orchestrator.py:317-318` writes one row, only on success, holding
`(ts, agent, request, response, tenant)` — no id linking it to anything, no
model, no actor, no status.

### Milestone B0 — Additive failure write on `TurnRepository` ✅

- **Failing test first:** `tests/test_orchestrator.py` —
  `test_a_failed_dispatch_is_persisted`: a `FakeLLM` that raises, assert the
  repository recorded one terminal row with `status="error"`. Red today
  (nothing is written on any failure path).
- **Depends on:** nothing — parallel-safe.
- Extend the `TurnRepository` Protocol the way `AsyncCloseableRepository`
  already extends it (`adapters/storage/base.py:41-55`): a new
  `@runtime_checkable` sub-Protocol, dispatched on with `hasattr`, so
  `SQLiteRepository` keeps satisfying the bare Protocol unchanged.
  `adapters/storage/base.py` is **not** a protected path.
- `tests/fakes.py` `FakeRepository` grows with the Protocol
  (`mango-fake-builder`).

### Milestone B1 — Schema version and join keys on the turn row — **partial**

- **Failing test first:** `tests/adapters/storage/test_sqlite.py` —
  `test_turn_row_carries_run_and_model_identity`: assert a saved row exposes
  `schema_version`, `run_id`, `task_id`, `trace_id`, `model`, `actor`. Red today.
- **Depends on:** B0.
- Follow `_ensure_tenant_column` (`sqlite.py:62-72`) — the repo's own additive
  in-place migration pattern — and its Postgres twin (`postgres.py:60`), which
  is already at parity and must stay there.
- This single milestone is what makes the cognitive record joinable to the side
  effect, and is the only thing that ever answers "which model produced this?"

### Milestone B2 — Thread `run_id` / `task_id` from the edge

- **Failing test first:** `tests/api/test_agents_routes.py` —
  `test_run_id_from_request_reaches_the_persisted_turn`. Red today.
- **Depends on:** B1.
- Accept both as optional request metadata, echo them on the access-log record
  (`api/middleware/access_log.py:62-69`), and pass them through
  `AgentRequest.metadata` — already an open dict (`core/agent.py:35`), so no
  protected-path edit.
- Removes the throwaway `uuid4()` mint at `cognitive/producer.py:75-87`:
  identity stops being per-call and starts being per-run.

### Milestone B3 — Actor on the record

- **Failing test first:** `tests/api/test_auth.py` —
  `test_authenticated_principal_is_recorded_on_the_turn`. Red today: `AuthState`
  is `(enabled, expected_token)` (`api/auth.py:52-57`) — there is no principal
  to record.
- **Depends on:** B1; **blocked on** the PR D decision about per-principal
  credentials. Honest status: this milestone cannot land as more than
  `actor="<shared-token>"` until that decision is made. Ship the column in B1
  regardless — a column with one honest value beats no column.

---

## PR C — Replay resistance and idempotency (spec-0034)

Every primitive this block needs is already built, tested, and unused. This is
wiring, not design — which is why it is cheap and why leaving it undone is hard
to justify.

### Milestone C0 — Enforce expiry at the sink boundary ✅

- **Failing test first:** `tests/cognitive/test_sink.py` —
  `test_expired_signal_is_rejected`: build a signal with `created_at` in the
  past, assert the sink refuses it. Red today — `is_expired()` exists
  (`cognitive_signal.py:172-174`) and has **zero callers** in `src/`.
- **Depends on:** nothing — parallel-safe.

### Milestone C1 — TTL becomes an operator tunable ✅

- **Failing test first:** `tests/config/test_signal.py` —
  `test_ttl_seconds_is_configurable`. Red today: `SignalSettings`
  (`config/signal.py:58-80`) has no TTL field and
  `cognitive/producer.py:227-242` never passes `ttl_seconds`, so every emitted
  signal is 24 h.
- **Depends on:** C0.
- `MANGOMAS_SIGNAL__TTL_SECONDS`, bounded by the envelope's own
  `1 … MAX_TTL_SECONDS`, per the `mango-config` skill.

### Milestone C2 — Dedupe on `signal_id` ✅

- **Failing test first:** `tests/cognitive/test_sink.py` —
  `test_replayed_signal_is_written_once`: emit the same envelope twice, assert
  one JSONL line and one POST. Red today — both sinks append/POST
  unconditionally (`cognitive/sink.py:57-60`, `:70-78`).
- **Depends on:** C0.
- A bounded seen-id set, plus an `Idempotency-Key` header on the HTTP sink
  derived from `signal_id`. Turns uniqueness into replay resistance.

### Milestone C3 — Give `ProposedAction` a producer

- **Failing test first:** `tests/cognitive/test_producer.py` —
  `test_planner_signal_carries_a_proposed_action_ref`. Red today: zero
  references to `ProposedAction` outside `tests/mango_contracts/`.
- **Depends on:** B2 (the key is derived from `run_id`/`task_id`, so it is only
  stable once those are real).
- Populate `recommendation.proposed_action_refs` and derive keys with
  `compute_idempotency_key` (`proposed_action.py:59-81`) — already correct,
  already tested, never called.

---

## PR D — Boundary honesty (spec-0035)

Each item here is a place where a name promises a control the code does not
implement. Cheap to fix, and until fixed a reader will reasonably over-trust the
system. Documentation-only items ship first because they carry no risk.

### Milestone D0 — Say what `policy_snapshot_hash` is ✅

- **Failing test first:** none — documentation. Verified by review.
- **Depends on:** nothing.
- `config/signal.py:40-42` computes `sha256("policy_id:policy_version")`. That
  is a checksum of two env vars, not a digest of a policy document, and
  `MANGOMAS_SIGNAL__POLICY_SNAPSHOT_HASH` accepts any 64-hex value. Say so at
  the definition and in the `mango-cognitive` skill: **provenance label, not
  attestation.** A verifier that treats it as proof of policy content is
  trusting a self-signed assertion.
- The real control — digesting an actual policy file — is deferred below.

### Milestone D1 — Say what tenancy is not ✅

- **Failing test first:** none — documentation.
- **Depends on:** nothing.
- `X-Tenant-ID` is client-asserted (`api/middleware/tenancy.py:34`), sanitised
  but unbound to any credential. With one shared token, any caller can name any
  tenant and read that tenant's history. The SQL isolation
  (`sqlite.py:122-131`) is real; the trust boundary is not. State plainly in
  `src/mangomas/tenancy.py` and the CLAUDE.md config table: **storage
  partitioning, not access control.**

### Milestone D2 — Gate inline workflow definitions ✅

- **Failing test first:** `tests/api/test_workflows_routes.py` —
  `test_inline_definition_is_refused_when_disallowed`. Red today.
- **Depends on:** nothing.
- `POST /workflows/run` executes a caller-supplied graph *even when*
  `MANGOMAS_WORKFLOW__ENABLED=false` — documented at
  `api/routes/workflows.py:40-41`, and surprising enough to deserve its own
  switch. Add `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION`, default `true` to
  preserve behaviour, flipped to `false` in `deploy/`.

### Milestone D3 — Model allowlist ✅

- **Failing test first:** `tests/composition/test_llm.py` —
  `test_model_override_outside_the_allowlist_is_refused`. Red today:
  `model_override` is an unconstrained `str | None` (`config/agents.py:40`).
- **Depends on:** B1 (allowlist constrains selection; the `model` column
  records it — the pair is what makes selection reviewable).
- `MANGOMAS_LLM__ALLOWED_MODELS`, empty = allow all, so today's behaviour is
  the default. Validate in `build_agent_llm_overrides`
  (`composition/llm.py:92-103`) and at base-client construction.

### Milestone D4 — Effects metadata on `ToolSpec` ✅

- **Failing test first:** `tests/test_tools.py` —
  `test_tool_spec_declares_effects`. Red today.
- **Depends on:** nothing.
- Add an optional `read_only` / `effects` field to `ToolSpec`
  (`core/tools.py:43-49`). **Note:** `core/tools.py` *is* a protected path, so
  this milestone — alone in the plan — needs a `BREAKING-CHANGE` commit
  message. The field is additive with a default, so no caller breaks.
- Prerequisite for any containment story once a write-capable tool is
  registered. Today `RetrievalTool` is read-only and nothing in the `Tool`
  protocol keeps it that way.

---

## Deferred / out of scope

- **Workflow run ledger** (`workflow_runs` / `workflow_steps` tables, node ids
  on `workflow/graph.py`, a persisting `NodeExecutor` decorator). The complete
  answer to audit §1, and a design change, not a wiring change. Needs **its own
  ADR** — the number is deliberately not allocated here, because ADR-0032 went
  to signal expiry and a placeholder that drifts is worse than none — and a
  spec before any code. PR B delivers most of the audit value at a fraction of
  the cost; re-open this only against that ADR.
- **Per-principal credentials** (subject, scopes, audience, expiry) replacing
  the single shared bearer. Revisiting **ADR-0014**, which chose the shared-token
  seam deliberately. B3 is blocked on this; D1 is the honest interim.
- **Signed cognitive envelopes.** The natural end state of D0, and only
  meaningful once a real policy document exists to digest. Revisit against
  ADR-0029 and the sibling harness's PDP — signing here without a verifier
  there is ceremony.
- **Compensation / outbox for tool side effects.** Correctly out of scope while
  no write-capable tool is registered; D4 is the trigger condition. Re-open the
  moment one is.
- **MCP supply-chain pinning.** `.mcp.json` pins every server version
  (`@2026.7.10`, `v0.20.1`) — good practice already — but `npx -y` / `uvx`
  resolve from the registry at run time with no integrity hash. A0 protects the
  file; hash pinning needs upstream support and is not actionable here.

## Verification

```bash
make gate          # the full pre-PR chain, in CI's order
```

Per-block, before pushing:

```bash
# PR A — the gates must still fire, and must fire on themselves
make protected-paths BASE_REF=origin/feat/initial-release
python -m pytest tests/harness tests/test_check_protected_paths.py \
                 tests/test_lint_agent_frontmatter.py tests/regression -q

# PR B — storage parity across both backends
python -m pytest tests/adapters/storage tests/test_orchestrator.py -q
RUN_POSTGRES=1 make postgres

# PR C — cognitive producer + sinks
python -m pytest tests/cognitive tests/mango_contracts -q

# PR D — API surface and composition
python -m pytest tests/api tests/composition tests/test_tools.py -q
```

Every milestone additionally runs its own mutation proof where it adds a guard
(`mango-mutation-proof`): back up, break the guarded thing, confirm the new test
goes red, restore. A2 exists **because** that step was skipped once already.
