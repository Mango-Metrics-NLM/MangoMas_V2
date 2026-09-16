# Spec-0031: Code quality, tech-debt reduction and enterprise readiness

- **Status:** Draft
- **Linked ADR:** ADR-0030 — required for the two protected-path touches:
  W7's `max_steps` ceiling on `core/agent.py`, and W3's `orchestrator.py` seam
  extraction. Every other workstream here is additive or mechanical.
- **Linked CHANGELOG entry:** `[Unreleased]` › `Changed` (on first landing)

## Problem

A full-repo reflection was run on 2026-09-16 against a clean working tree at
`df92e3d`. Its first finding is that **the gate is already green** — all twelve
`make gate` steps pass locally, 2683 tests pass, and coverage sits at 98.88%
against a 95% floor. This is not a remediation program for broken code.

Its second finding reorders everything else: **the HTTP surface has an
unauthenticated cluster that the feature flags do not gate.** Verified by
running the app in-process against stock defaults — `WORKFLOW__ENABLED=false`,
`AUTH__ENABLED=false`, `API__MAX_BODY_BYTES=0` — a client can execute arbitrary
agent topologies, probe the filesystem for existence, and escape the error
boundary with a 16 KB body. W7 therefore lands first, not last.

The rest of the debt is **slack between what the code already achieves and what
the gates actually require**, plus a set of fail-open holes in the governance
layer that guards it. A gate that passes because its subject silently vanished
is the defect class specs 0020 and 0021 were each written to hunt after the
fact; this spec finds five more instances and closes them before they fire.

The measured baseline is recorded in
[`docs/plans/20260916T000000Z-code-quality-tech-debt-plan.md`](../docs/plans/20260916T000000Z-code-quality-tech-debt-plan.md).

## Requirements

Eight workstreams, delivered in the order **W7 → W4 → W1 → W2 → W3 → W6 → W5 →
W8**. Each is independently landable and each must leave `make gate` green.

- **W7 — Runtime hardening (first).** Make the workflow flag gate the HTTP
  surface; remove filesystem reads driven by a request body; bound graph depth,
  node count and `max_steps`; harden the reference deployment manifest and the
  default docs exposure; stop leaking upstream URLs, project ids and raw driver
  text into unauthenticated bodies; contain secrets behind `SecretStr`.
- **W1 — CI/CD economics and integrity.** Add `concurrency:` groups and
  `timeout-minutes` to all four workflows; make `deploy.yml`'s `verify` job run
  the real gate; close the `sbom-scan` fail-open that mutes the nightly
  reporter; cache the type-checker and linter caches.
- **W2 — Dependency determinism.** CI installs the dev extra unconstrained, so
  a green pipeline can turn red from an upstream release with no local change.
  Constrain it, and make new deprecation warnings visible rather than silent.
- **W3 — Oversized-module reduction.** Five modules, in ascending risk order,
  ending at `core/orchestrator.py`.
- **W4 — Gate-integrity ratchets.** Raise every per-package coverage floor to
  the level the code already holds, and make three hand-maintained registries
  self-checking.
- **W5 — Hard-coded values.** Close the drift between `Settings` defaults,
  `.env.example` and the `CLAUDE.md` table, and move genuine tunables into
  `Settings`.
- **W6 — Dead and redundant code.** Remove unreferenced symbols and vestigial
  shims; collapse measured duplication clusters; correct four documents that
  describe a defence layer with no invoker.
- **W8 — Enterprise organisation.** Release mechanics, documentation entry
  points, and repository-layout consistency.

W1–W6 and W8 must be **additive or mechanical**: none changes runtime behaviour
when configuration is untouched.

**W7 is the deliberate exception, and it is a breaking change by intent.** The
whole point is that requests which succeed today must stop succeeding: an
inline workflow definition with the feature disabled, a `max_steps` of one
billion, a 16 KB nested graph, a filesystem path in a request body. Each
refusal is a named scenario below with a regression test, and each is called
out in `CHANGELOG.md` under `Changed` with the migration note — an operator who
was relying on per-invocation workflow opt-in over HTTP sets
`MANGOMAS_WORKFLOW__ENABLED=true`.

## Scenarios (WHEN/THEN)

Every scenario below was **verified against the running application**, not
inferred from source. Each is stated in both directions per the template's rule
that a gate must be proven able to fire.

### The unauthenticated HTTP cluster (W7)

**S7 — the workflow flag must gate the HTTP surface.**
`workflow/loader.py:35-45` consults `cfg.enabled` only when `definition is
None`. The docstring calls this "per-invocation opt-in", which is correct for
the CLI, where the caller is the operator. On the HTTP surface the caller is
anonymous.

- WHEN `workflow.enabled` is `false` and a request body carries an inline
  `definition`, THEN `/workflows/run` must refuse. *Verified today: it returns
  **200** and executes the graph over every registered agent.*
- WHEN `workflow.enabled` is `true`, THEN it runs.

**S8 — a request body must not name a filesystem path.** `loader.py:63-77`
reads any `definition` not starting with `{` via `Path(stripped).read_text()`
— no allow-list, no root confinement, no size cap, and synchronously inside an
async route.

- WHEN two paths differ only in existence, THEN the responses must be
  indistinguishable. *Verified today they are not:* `/etc/passwd` returns "not
  valid JSON" (read succeeded) while `/nonexistent/zz` returns "cannot read …
  No such file or directory" — a full-filesystem existence oracle.
- WHEN a definition is inline JSON, THEN it parses normally.

**S9 — graph nesting must be bounded.** `WorkflowStep` includes `FanOutNode`
and `BranchNode`, so both recurse without limit — contradicting the comment at
`graph.py:81-83` that "v1 keeps nesting bounded to depth two, so no recursion
is possible".

- WHEN a deeply nested graph is submitted, THEN `load_workflow` must raise
  `ConfigError`. *Verified today it raises `RecursionError`, which derives from
  `RuntimeError` and so escapes the `ConfigError` boundary entirely — at depth
  500, a **16 KB** body. With `max_body_bytes=0` by default this is reachable
  unauthenticated.*
- WHEN a graph is within the bound, THEN it validates.

**S10 — `max_steps` must have a ceiling.** `core/agent.py:36` declares `ge=1`
with no `le`, and `orchestrator.py:322-338` returns the request value verbatim.

- WHEN a request sets `max_steps` above the configured ceiling, THEN it must be
  rejected or clamped. *Verified today `AgentRequest(max_steps=10**9)` is
  accepted, and each iteration also appends an assistant message, so the prompt
  grows alongside the call count.*
- WHEN `max_steps` is within the ceiling, THEN the loop runs as before.

### The gate-integrity holes (W4)

**S1 — protected-path governance survives a module→package conversion.**
`scripts/check_protected_paths.py:128` computes `set(changed) & protected_paths`
— an exact string intersection against the `pyproject.toml` table.

- WHEN `src/mangomas/core/orchestrator.py` is converted to a package and a file
  under it is edited without a `BREAKING-CHANGE` trailer, THEN the gate must
  fail. *Today it prints "No protected core contracts changed. OK." and exits 0.*
- WHEN no protected path is touched, THEN the gate still passes.

**S2 — the facade registry cannot silently stop covering a package.**
`tests/test_import_compat.py:45`'s `_FACADES` dict is hand-maintained. It is
complete today — verified: cli 8/8, telemetry 6/6, config 13/13,
composition 12/12, api.middleware 3/3.

- WHEN a new submodule is added to a registered facade package and is not added
  to `_FACADES`, THEN the contract test must fail. *Today nothing notices.*
- WHEN every on-disk submodule is registered, THEN it passes.

**S3 — the pre-commit tool revisions cannot drift from the pyproject pins.**
`.pre-commit-config.yaml` documents `ruff` and `mypy` revs as kept "in lockstep"
with the `==` pins in the dev extra. `tests/tooling/test_precommit_parity.py`
has four tests; none of them checks this.

- WHEN `ruff==` in `pyproject.toml` and `rev:` in `.pre-commit-config.yaml`
  disagree, THEN a test must fail. *Today the hook silently runs a different
  linter than CI — the "green locally, red in CI" class.*
- WHEN they agree, THEN it passes.

**S4 — the nightly reporter can actually report.** `nightly.yml:127` sets
`continue-on-error: true` on `sbom-scan`, which is in the `notify` job's
`needs:` list. The job can therefore never reach a failed conclusion, so
`if: failure()` never fires for it.

- WHEN `sbom-scan` fails, THEN `notify` must open an issue.
- WHEN every nightly job passes, THEN no issue is opened.

`tests/deploy/test_workflow_hardening.py:234` asserts membership in `needs:`,
not that the job can fail — which is exactly why it passes today.

**S5 — a documented setting must reach a consumer.** Three `ApiSettings`
fields resolve, parse, are documented in both `CLAUDE.md` and `.env.example`,
and are read by nothing: `ready_timeout_seconds`, `host`, `port`. Verified by
grep across `src/` — the only `asyncio.timeout` in the package is
`core/orchestrator.py:354`, the loop step timeout.

The consequence is a live defect, not just dead config.
`api/health.py:77-123` awaits `ctx.llm.ping()` and `ctx.repo.list_turns(limit=1)`
**unbounded**, so the effective budget for the unauthenticated `/readyz` probe
is `MANGOMAS_LLM__TIMEOUT_SECONDS` (60.0s), not the documented 2.0s — a 30×
gap. No test references `ready_timeout_seconds` at all. `host` and `port` are
worse-shaped: they are *uncommented* in `.env.example:63-64`, so they read as
live knobs, while the real serving contract is the plain `PORT` variable
(`Dockerfile:31,62`).

- WHEN a `Settings` field is documented in `.env.example` or `CLAUDE.md` and no
  module outside `config/` reads it, THEN a test must fail. *Today
  `test_env_example_contract.py` checks name↔field resolution in both
  directions but never field↔consumer, which is the blind spot these three sit
  in.*
- WHEN every documented field has a consumer, THEN it passes.

**S6 — `.env.example` values must not contradict the defaults.** The contract
test compares `CLAUDE.md`'s default *column* to `model_fields`, but never
`.env.example`'s *values*. Four lines state a value that is not the default:
`LOG__BODY_TRUNCATE=2000` (real 512), `API__READY_TIMEOUT_SECONDS=5.0` (real
2.0), `API__HISTORY_DEFAULT_LIMIT=50` (real 10), `API__HISTORY_MAX_LIMIT=500`
(real 1000). All four are commented, and the file's header says commented
blocks are opt-in features — but sibling commented lines in the same blocks
(`LOG__FORMAT=text`) *do* restate the real default, so nothing tells a reader
which kind of line they are looking at.

- WHEN an `.env.example` line states a value differing from the field default
  and is not on the recorded example allowlist, THEN a test must fail.
- WHEN values agree or the line is an allowlisted illustrative override
  (the vertex / gcp / postgres / eval-threshold blocks, which legitimately show
  non-defaults), THEN it passes.

## Config / env additions

No env var is added by W1–W4, W6 or W8. W5 and W7 add the tunables their own
audits name; each follows the existing rule — a `DEFAULT_*` module constant
surfaced through a `Settings` group, documented in the same commit in all three
places (`config/`, `.env.example`, `CLAUDE.md`), so
`tests/deploy/test_env_example_contract.py` stays green in both directions.

The one *removal*: none. Retiring an inert setting is a separate, reviewed act
(see `MANGOMAS_RAG__MIN_CHUNK_WORDS`, retired at `f8d37a1`).

## Protocol / contract impact

- **New/changed protocols:** none.
- **New error types:** none. W7's bounds reuse `ConfigError` (400) and the
  existing 413/503 backpressure envelopes.
- **Registry additions:** none.
- **Protected paths — two touches, both needing a `BREAKING-CHANGE` trailer and
  ADR-0030:**
  1. W7's `max_steps` ceiling adds `le=` to `AgentRequest.max_steps` in
     `src/mangomas/core/agent.py`, and clamps in `core/orchestrator.py`.
  2. W3 adds `src/mangomas/core/_topology.py` to
     `[tool.mangomas.governance].protected_paths` **in the same commit that
     creates it**, with a pin test mirroring
     `test_core_structured_is_a_protected_path`. The governance table grows; it
     never shrinks.
- **Wire contract:** `tests/test_openapi_snapshot.py` must stay green **without
  regeneration for every workstream except W7's `max_steps` ceiling**, which
  narrows a DTO field's validation range and therefore legitimately reshapes the
  schema. That single regeneration is the review record for the contract change,
  per the snapshot module's own docstring. Any *other* workstream needing a
  regeneration is a signal that the change exceeded this spec's scope.

## Backwards-compatibility

- Every existing import path keeps working. W3 extractions leave a permanent
  re-export facade per ADR-0019, and each new facade is registered in
  `_FACADES` in the same commit — which W4's completeness test then enforces.
- `from mangomas.core.orchestrator import FanOutOutcome` and
  `from mangomas.core import FanOutOutcome` both survive the W3 extraction
  (plan PR E-M5).
- `EvalReport` is a **persisted artifact schema**, read back by
  `eval/baseline.py`. Any W3 move relocates the class, never its field set.
- The `combine-as-imports` change (plan PR E-M1) is formatting only. Verified on
  `config/__init__.py`: 128 import bindings and 127 `__all__` entries identical
  before and after; 533 lines become 305.
- No symbol is removed from a public surface, so no import-level migration is
  required.
- **W7 is the one behavioural break, by intent** (see Requirements). Four
  request shapes that succeed today must fail afterwards. The migration is
  configuration, not code: an operator relying on per-invocation workflow
  opt-in over HTTP sets `MANGOMAS_WORKFLOW__ENABLED=true`; one relying on a
  `max_steps` above the new ceiling raises `MANGOMAS_LOOP__MAX_STEPS`. Both go
  in `CHANGELOG.md` under `Changed` with the note, not under `Fixed`.
- The W6 removals each need a recorded decision before landing, because two of
  them are compat surface rather than dead weight: `EvalRunner.run`'s legacy
  `agent_name` positional (no production caller, ~18 test call sites) and the
  `# approved-breaking-change` marker alias (zero occurrences in 267 commits).

## Test plan

- **Unit:** each workstream extends the suite that already owns its surface —
  `tests/test_workflow_http.py` and `tests/test_api.py` for W7,
  `tests/deploy/` for W1/W2, `tests/test_import_compat.py` for W3/W4,
  `tests/tooling/` for the parity pins, `tests/deploy/test_env_example_contract.py`
  for W5.
- **Gates:** S1–S10 are each written as a two-sided test before the fix, per the
  `mango-mutation-proof` skill. S7–S10 already have a verified reproduction —
  the in-process check that found them becomes the test, so the red direction is
  established rather than asserted. S1's negative direction is proven by
  converting a **scratch copy**, never the real module, to a package and
  asserting the gate fails.
- **Coverage:** W4 raises floors to `measured − 2`, the two-point margin the
  `SCRIPTS_FLOOR` comment already established as this repo's convention. No
  floor is set to its exact measured value, because a one-point fluctuation
  would then break CI for no defect.
- **Gated suites:** untouched. No workstream needs LM Studio, Vertex, Postgres
  or a live GCP project.

## Acceptance criteria

- [ ] `make gate` green at every landing, not merely at the end.
- [ ] S1–S10 each have a test that fails before the fix and passes after
      (mutation-proven, both directions).
- [ ] With stock defaults, `/workflows/run` refuses an inline definition,
      `/workflows/validate` returns one indistinguishable error for any
      filesystem path, a 16 KB nested graph raises `ConfigError` not
      `RecursionError`, and `AgentRequest(max_steps=10**9)` is rejected.
- [ ] `/docs`, `/redoc` and `/openapi.json` are unreachable when
      `MANGOMAS_ENV != "local"`; `GET /agents` requires auth when auth is on.
- [ ] `deploy/service.yaml` sets auth and both backpressure knobs, and
      `deploy/README.md` documents the required set.
- [ ] `repr(Settings(...))` contains no credential value.
- [ ] No per-package coverage floor is lower than `measured − 2`.
- [ ] `tests/test_openapi_snapshot.py` passes without regeneration, except for
      the single reviewed regeneration carrying W7's `max_steps` ceiling.
- [ ] Every W3 extraction is registered in `_FACADES` and covered by the new
      completeness test.
- [ ] `CHANGELOG.md` `[Unreleased]` names the test for each landed workstream,
      with W7's four refusals under `Changed` and their configuration migration.
- [ ] ADR-0030 recorded before either protected-path touch lands;
      `BREAKING-CHANGE` trailer present on both commits.
- [ ] The four documents describing `--check-protected-paths` as a live
      pre-commit hook either become true or are corrected.
