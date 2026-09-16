# Spec-0031: Code quality, tech-debt reduction and enterprise readiness

- **Status:** Draft (revision 3 — peer-reviewed and fact-checked; see "Review record")
- **Linked ADRs:** four are required, not one. See "Protocol / contract impact".
- **Linked CHANGELOG entry:** `[Unreleased]` › `Changed` (on first landing)

## Problem

A full-repo reflection was run on 2026-09-16 against a clean working tree at
`df92e3d`. Its first finding is that **the gate is already green** — all twelve
`make gate` steps pass, 2683 tests pass, coverage sits at 98.88% against a 95%
floor, and `mypy --strict` is clean over 433 files. This is not a remediation
program for broken code.

The debt is **slack between what the code already achieves and what the gates
actually require**, plus a set of guards that pass because their subject is
absent rather than because it is sound. That is the defect class specs 0020 and
0021 were each written to hunt after the fact. This spec finds six more, one of
which is in the wire-contract guard itself.

There is also a set of questions that are not bugs at all but **accepted
decisions whose threat model was never written down**. Separating those from real
defects is the main thing revision 2 fixes; revision 1 conflated them and
overstated the result.

And there are **six reproduced runtime defects** — five of them found only after
a pass that deliberately avoided every area the first audit had covered. That is
the most useful thing this reflection produced, and the reason is worth stating:
the first audit looked where the repo already looks at itself. Its governance,
coverage and lint surfaces are heavily instrumented and came back clean, while
`fail_fast`, the 404 envelope, the Postgres pool, the CLI telemetry path and the
RAG ingestion pipeline are not instrumented and are where the defects were.

## What this spec does *not* claim

Revision 1 asserted an "unauthenticated HTTP cluster". That was wrong and is
withdrawn. The auth seam works. Verified by running the app with
`MANGOMAS_AUTH__ENABLED=true`:

| Route | Without credentials |
|---|---|
| `POST /workflows/run`, `POST /workflows/validate` | **401** |
| `POST /agents/{name}/invoke`, `/stream`, `GET /history` | **401** |
| `GET /healthz`, `/health`, `/readyz`, `/ready` | 200 — deliberate, ADR-0014 |
| `GET /agents` | 200 — deliberate, ADR-0014:44-45 |
| `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` | 200 — **never configured** |

So the workflow findings are reachable only when auth is off. That is the
documented default for a local-first platform (ADR-0014), not a bypass.

Revision 2 then called `GET /agents` an unexplained exemption and the reference
manifest an "unauthenticated LLM proxy". A fact-check falsified both:
ADR-0014:44-45 decides the roster route explicitly, and the deploy path keeps the
service private by Cloud Run IAM. Only the FastAPI docs endpoints remain
genuinely unconsidered — `create_app` never passes `docs_url`/`redoc_url`/
`openapi_url`, and no document records a decision either way.

## Requirements

Eight workstreams, ordered by how much of each is settled fact rather than open
question: **R1 → R2 → R3 → R4 → R5 → R6 → R7 → R8**.

- **R1 — Bound the workflow graph.** An unambiguous bug with no decision behind
  it. Depth, node count and source size.
- **R2′ — Correctness defects in shipped features.** Five features that do not do
  what they say, found only after a pass that deliberately avoided every area the
  first audit covered. Each is reproduced; see S13–S17.
- **R2 — Correct the default and reference posture.** The docs endpoints, the
  readiness probe's unwired timeout, and the app-layer defences missing from the
  reference manifest. `GET /agents` was withdrawn from this list — see S2.
- **R3 — Gate integrity.** Six guards that cannot currently fire, including the
  wire-contract guard's blindness to constraint narrowing.
- **R4 — CI/CD economics and release integrity.** Concurrency, timeouts, the
  deploy gate, the nightly reporter's fail-open, analyser caching.
- **R5 — Dependency determinism.** CI installs the dev extra unconstrained.
- **R6 — Oversized-module reduction.** Five modules in ascending risk order.
- **R7 — Inert and hard-coded configuration.** Five settings with no effective
  consumer; 27 `.env.example` values contradicting their defaults, of which the
  allowlist decision is the real work.
- **R8 — Dead code and enterprise organisation.** Surface that outlived its
  consumer; release mechanics; documentation entry points.

**Two open decisions are carried separately** (see "Decisions required"), because
they are not defects and cannot be fixed by a patch: the per-invocation workflow
opt-in over HTTP, and the absence of a loop-budget ceiling.

R3–R8 must be additive or mechanical: none changes runtime behaviour when
configuration is untouched. R2 and R2′ change behaviour by intent, each with a
regression test and a `CHANGELOG` entry under `Changed`. R1 is a **bug fix, not a
break**: a 16 KB nested graph returns 500 today, so turning it into a 400 needs no
migration note — revision 2 wrongly listed it among the breaking shapes.

## Scenarios (WHEN/THEN)

Each scenario below is marked **[run]** if it has a recorded reproduction
transcript, or **[read]** if it is a source reading. Revision 1 labelled
everything "verified against the running application"; that was not true of all
of them, and the distinction is restored here.

### R1 — the one unambiguous bug

**S1 — graph nesting must be bounded. [run]** `workflow/graph.py:81-86`'s comment
claims "v1 keeps nesting bounded to depth two, so no recursion is possible".
`WorkflowStep` includes `FanOutNode` and `BranchNode`, so both recurse without
limit and the comment is false.

| Nesting depth | Body size | `load_workflow` result |
|---|---|---|
| 200 | ~6 KB | validates cleanly |
| 500 | 16 KB | `RecursionError` |
| 2000 | 64 KB | `RecursionError` |
| 5000 | 160 KB | `RecursionError` |

- WHEN a graph exceeds the bound, THEN `load_workflow` raises `ConfigError`.
  *Today it raises `RecursionError`, which derives from `RuntimeError`, so it is
  not caught by `except json.JSONDecodeError` and never reaches the `ConfigError`
  normalisation — it surfaces as a 500 through the access-log middleware.*
- WHEN a graph is within the bound, THEN it validates unchanged.

This is the only finding in the whole audit with no closed decision behind it,
no protected path, and no ADR to supersede.

**Two corrections from the fact-check.** First, the comment actually reads "no
recursion **/ cycle** is possible"; revisions 1 and 2 quoted it with the clause
that *is* true removed. Second, the `RecursionError` originates in `json.loads`
inside `_parse_json`, not in `model_validate`, so a depth counter placed "before
`model_validate`" can never run — only a byte cap on `source` and catching
`RecursionError` can execute. Between roughly depth 300 and 400 the loader
already returns a clean `ConfigError` via pydantic-core's own guard, so the gap
is in one stage rather than the whole path. This also means the case does **not**
belong in the "requests that succeed today must stop" list: a 16 KB graph returns
500 today, and turning that into a 400 is a bug fix needing no migration note.

### R2′ — shipped features that do not do what they say

**S13 — `fail_fast` must stop the run. [run]** `eval/runner.py:307` checks
`stop_event.is_set()` **before** `async with semaphore` at `:317`, while
`asyncio.gather` has already scheduled every worker — so by the time row 1 fails
and sets the event, every other worker has passed its check and merely waits on
the semaphore.

| Parallelism | Fake agent awaits | Rows that ran | Skipped |
|---|---|---|---|
| 1 | no | 1 | 9 |
| 1 | **yes** | **10** | **0** |
| 4 | **yes** | **10** | **0** |

- WHEN `fail_fast` is set and a row fails, THEN no further row is dispatched.
  *Today every remaining row runs, because any real LLM call suspends. The
  `{"skipped": True}` branch is dead code, and `MANGOMAS_EVAL__FAIL_FAST=true`
  burns the whole dataset's spend on every CI run.*
- WHEN `fail_fast` is unset, THEN every row runs as today.

`tests/eval/test_runner.py:106` covers this and passes **only** because its fake
agent contains no suspension point — the clearest case in the repo of a green
test over a broken feature.

**S14 — a 404 must carry a clean `message`. [run]** `errors.py:113` declares
`AgentNotFound(MangomasError, KeyError)` so `except KeyError` callers keep
working. `MangomasError` defines no `__str__`, so the MRO resolves it to
`KeyError.__str__`, which returns `repr(args[0])`:

```
POST /agents/nosuch/invoke  ->  404
{"error":"agent_not_found","message":"\"Unknown agent: 'nosuch'\"","detail":"agent_name='nosuch'"}
```

- WHEN a 404 is returned, THEN `message` carries no embedded quoting. *Today
  every 404 from `/agents/{name}/invoke`, `/stream` and both workflow routes ships
  the doubled form; `ConfigError` and siblings are unaffected.*
- No test asserts `body["message"]` anywhere — all three assert only
  `body["error"]` — and S6's snapshot pins property names, not values, so
  nothing mechanical sees it.

**S15 — the Postgres statement timeout must apply to the pool. [read]**
`postgres.py:146-152` runs `SET statement_timeout` on one borrowed connection.
The setting is session-scoped, so the other `pool_min..pool_max` connections never
receive it, and asyncpg's `Connection.reset()` issues `RESET ALL` on release,
discarding it even there.

- WHEN `MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS` is set, THEN every pooled
  connection enforces it. *Today the documented knob does nothing.*

**S16 — CLI dispatch must record metrics. [run]** `configure_metrics` has exactly
one call site in `src/`: `api/app.py:115`. `cli/_runtime.py:86` calls
`configure_telemetry` and never `configure_metrics`.

- WHEN metrics are enabled and an agent is dispatched from the CLI, THEN a real
  `MeterProvider` is installed. *Today `_state.metrics_configured` is `False`
  after CLI bootstrap and the global provider is `_ProxyMeterProvider`, so every
  `mangomas chat` / `eval` / `workflow run` metric is dropped — contradicting the
  ADR-0026 comment at `core/orchestrator.py:26-31` that "every dispatch path —
  HTTP, CLI, workflow nodes … records unconditionally here."*

**S17 — re-ingesting must not destroy the index on failure. [read]**
`rag/pipeline.py:117` deletes a source's vectors, then chunks, embeds (`:142`)
and upserts, with no transaction and no rollback.

- WHEN the embedding provider fails between the delete and the upsert, THEN the
  prior vectors survive. *Today they are gone with nothing replacing them, so
  running `mangomas rag ingest` during an LLM outage is destructive.*

### R2 — default and reference posture

**S2 — `GET /agents` ⛔ WITHDRAWN.** Revisions 1 and 2 called this an
unexplained exemption. It is a recorded decision:
`docs/adr/0014-application-auth-seam.md:44-45` — "Probes (`/healthz`, `/readyz` +
aliases) and **`GET /agents` stay unauthenticated so Cloud Run health checks and
discovery keep working**", restated at `api/routes/system.py:3-5`. Gating it
supersedes ADR-0014 and belongs with D1 and D2 as an open decision, not in a
hardening workstream.

**S3 — the API schema must not be public by default. [run]** `create_app` never
passes `docs_url`/`redoc_url`/`openapi_url`, so all four FastAPI defaults serve
200 regardless of auth.

- WHEN `MANGOMAS_ENV != "local"`, THEN `/docs`, `/redoc` and `/openapi.json` are
  unreachable.
- WHEN `MANGOMAS_ENV == "local"`, THEN they serve as today.

**S4 — the readiness probe must honour its own budget. [read]**
`MANGOMAS_API__READY_TIMEOUT_SECONDS` is declared at `config/api.py:19,57`,
documented in `CLAUDE.md` as the "`/readyz` LLM-ping budget" and in
`.env.example:65` — and read nowhere outside `config/`. `api/health.py:77-123`
awaits `ctx.llm.ping()` and `ctx.repo.list_turns(limit=1)` unbounded, so the
effective budget on an unauthenticated probe is `llm.timeout_seconds` (60.0s),
30× the documented 2.0s. The only `asyncio.timeout` in the package is
`orchestrator.py:354`. No test references the field.

- WHEN a readiness dependency hangs past the budget, THEN `/readyz` reports it
  as failed within the budget.
- WHEN dependencies respond, THEN the report is unchanged.

**Wiring note.** `check_ready(orchestrator)` takes only an orchestrator and never
calls `get_settings()`. Reaching for settings inside `health.py` would put a
service locator in a helper module, against the composition-root rule. Follow the
existing precedent instead: `create_app` already does
`app.state.auth = resolve_auth_state(_settings)` at `api/app.py:177`, so store
the resolved timeout on `app.state` the same way and pass it from the route.

**S5 — the reference manifest should carry the app-layer defences. [read]**
`deploy/service.yaml` sets eight `MANGOMAS_*` vars and none of `AUTH__ENABLED`,
`AUTH__SECRET_REF`, `API__MAX_BODY_BYTES` or `API__MAX_CONCURRENT_REQUESTS`, and
its only annotations are autoscaling bounds, so there is no ingress restriction.

**The severity claimed in revisions 1 and 2 is withdrawn.** They said an operator
following it "publishes an unauthenticated LLM proxy". That is false: the deploy
path leaves the service **private by Cloud Run IAM**, stated where the manifest
is applied (`deploy.yml:114-116` — "`replace` never touches IAM: it creates no
allUsers invoker binding, so the service stays private") and again at `:122-128`,
and pinned by `tests/deploy/test_deploy_contract.py`. The related claim that
`deploy/README.md` "mentions only CORS" is also false — it carries a 17-row
settings table including `MANGOMAS_AUTH__` and a recommended production baseline.

- WHEN the manifest is applied, THEN the app-layer defences are on as well as the
  platform-layer one. This is defence in depth, not an exposure, and it does not
  justify reordering the plan.

### R3 — guards that cannot fire

**S6 — the wire-contract guard must see a narrowed constraint. [run]** This is a
new finding produced by peer-reviewing revision 1, and it is the most important
one in R3. `tests/test_openapi_snapshot.py:46-62` pins a normalized projection of
property **names** and **required** sets only. Adding an upper bound to a DTO
field emits a `maximum` keyword *inside* the property schema, which the
projection discards.

Measured by adding `le=100` to `AgentRequest.max_steps` in memory and rebuilding:

```
raw schema:  {'type': 'integer', 'maximum': 100.0, 'minimum': 1.0, ...}
projection changed?  NO
```

The snapshot's own docstring says it exists because "nothing mechanical noticed a
DTO field turning required, a route disappearing, or a schema being reshaped".
**Constraint narrowing is a fourth case it never covered.** Any PR that tightens
`le`/`ge`/`max_length`/`enum` on a published DTO ships with no diff and no review
record.

- WHEN a published DTO field's validation range is narrowed, THEN the snapshot
  test fails until regenerated.
- WHEN nothing narrows, THEN it passes.

**S7 — protected-path governance must survive a package conversion. [run]**
`scripts/check_protected_paths.py:128` computes `set(changed) & protected_paths`
— an exact string intersection against the `pyproject.toml` table.

- WHEN a protected module becomes a package and a file under it is edited without
  a `BREAKING-CHANGE` trailer, THEN the gate fails. *Today it prints "No protected
  core contracts changed. OK." and exits 0.*
- WHEN nothing protected is touched, THEN it still passes.

**Scope correction.** Revision 1 proposed adding one new row to the governance
table per extraction. Prefer **glob matching**: replace the four `core/*.py` rows
with `src/mangomas/core/**/*.py`. That closes the package-conversion hole, the
new-sibling hole, and the currently-unprotected `core/__init__.py` and
`core/loop.py` in one change, and it stops the table growing by a row per future
split — which matters given that the governance meta-layer is already the
highest-churn part of this repo.

**S8 — the facade registry must notice an unregistered facade. [run]**
`tests/test_import_compat.py:45` keys `_FACADES` on five packages: `cli`,
`telemetry`, `config`, `composition`, `api.middleware`. Each is complete today
(8/8, 6/6, 13/13, 12/12, 3/3).

**Revision 1's version of this test was useless for its stated purpose.** It
checked that a *registered* package's submodules are all listed. Every extraction
R6 proposes lands in an **unregistered** package — `adapters.llm`, `cognitive`,
`core`, `scripts`, `tests.constants`. `mangomas.core` is absent from `_FACADES`
entirely, which means the `tools.py` → `structured.py` precedent R6 cites as its
model was never registered either.

- WHEN a package's `__init__.py` re-exports names it does not define and the
  package is absent from `_FACADES`, THEN the test fails.
- WHEN every such package is registered and complete, THEN it passes.

**Not the free ratchet it was billed as.** Implemented literally over on-disk
submodules, the test is RED on `mangomas.cli` today: 9 modules exist, the 8
registered plus `main`, which *is* the facade (`_FACADE_MODULES`,
`test_import_compat.py:105`); a recursive walk also surfaces the `commands`
subpackage `__init__`. Both need explicit exclusion, and that exclusion rule is
the design work.

**S9 — pre-commit revisions must match the pyproject pins. [read]** Both files
carry a "keep in lockstep" comment; `tests/tooling/test_precommit_parity.py`'s
four tests never check it. The identical duplication for the coverage floor *is*
guarded at `test_ci_make_parity.py:226`.

- WHEN `ruff==` / `mypy==` and the corresponding `rev:` disagree, THEN a test
  fails. *Today the hook can silently run a different linter than CI.*
- WHEN they agree, THEN it passes.

**S10 — the nightly reporter must be able to report. [read]**
`nightly.yml:127` sets `continue-on-error: true` on `sbom-scan` while listing it
in `notify`'s `needs:`, so it can never reach a failed conclusion and
`if: failure()` never fires for it. The existing guard
(`test_workflow_hardening.py:234`) asserts `needs:` membership, not that the job
can fail.

- WHEN a job in `notify`'s `needs:` sets `continue-on-error`, THEN a test fails.
- WHEN none does, THEN it passes.

**S11 — `.env.example` values must not contradict the defaults. [run]** The
contract test compares `CLAUDE.md`'s default column to `model_fields`, never
`.env.example`'s values.

| Line | States | Real default |
|---|---|---|
| `LOG__BODY_TRUNCATE` | 2000 | 512 |
| `API__READY_TIMEOUT_SECONDS` | 5.0 | 2.0 |
| `API__HISTORY_DEFAULT_LIMIT` | 50 | 10 |
| `API__HISTORY_MAX_LIMIT` | 500 | 1000 |

- WHEN a line states a value differing from the field default and is not on the
  recorded example allowlist, THEN a test fails.
- WHEN values agree, or the line is an allowlisted illustrative override, THEN it
  passes.

**The red set is 27 lines, not four.** A full comparison of every `MANGOMAS_*`
line finds 27 mismatches, and at least six sit outside the allowlist revisions 1
and 2 proposed: `API__CORS_ALLOW_ORIGINS`, `API__CORS_ALLOW_METHODS`,
`AUTH__SECRET_REF`, `EMBEDDINGS__DEVICE`, `WORKFLOW__DEFINITION`,
`EVAL__DATASET_PATH`. The test is trivial; **deciding which lines are
illustrative is the deliverable.**

**S12 — a documented setting must reach a consumer. [run]** Three `ApiSettings`
fields resolve, parse and are documented, and are read by nothing outside
`config/`: `ready_timeout_seconds` (S4), `host`, `port`. A fourth,
`log.body_truncate`, is the same shape. `host`/`port` are *uncommented* in
`.env.example:63-64`, so they read as live knobs while the real serving contract
is the plain `PORT` variable (`Dockerfile:31,62`).

- WHEN a documented field has no consumer outside `config/`, THEN a test fails.
- WHEN every documented field is consumed, THEN it passes.

## Decisions required (not defects)

These two are carried as questions, not milestones. Revision 1 filed both as
security fixes; both are in fact recorded decisions, and treating them as bugs
was the single largest error in it.

### D1 — per-invocation workflow opt-in over HTTP

`workflow/loader.py:35-45` runs a per-request `definition` even when
`workflow.enabled` is false. This is **not** an accidental flag bypass:

- `specs/0008-workflow-http-endpoint.md:24-26` states it as a requirement:
  "a per-request `definition` (inline JSON or path) runs even when the feature is
  disabled (per-invocation opt-in)". The spec's status is **Implemented**.
- Its acceptance criterion is ticked: "[x] A per-request `definition` runs a
  graph over HTTP regardless of the enabled flag".
- `docs/adr/0012-workflow-http-endpoint.md:47-50` considered and **rejected** the
  exact remedy revision 1 proposed: "**Conditionally mount routes when
  `enabled`** — rejected: a disabled deployment would 404 and lose the
  per-request `definition` opt-in the CLI already offers."
- Three tests in `tests/test_workflow_api.py` pin the behaviour, and its module
  docstring states it.

What is genuinely missing is that **ADR-0012's trade-off section considers only
400-vs-404 error modelling and contains no threat analysis**, and spec-0008 has
no security section. The same applies to the path-reading half: spec-0008 says
"inline JSON or path", so `Path(stripped).read_text()` on a request body — no
allow-list, no root confinement, no size cap, and synchronous inside an async
route — is also specified rather than accidental. Its observable consequence is
a filesystem existence oracle, verified: `/etc/passwd` returns "not valid JSON"
(the read succeeded) while `/nonexistent/zz` returns "cannot read … No such file
or directory".

**Recommended resolution:** do not reverse ADR-0012. Add an additive
`WorkflowSettings.allow_inline_definition: bool = False`, which is config-driven,
default-safe under this repo's additive rule, needs no supersession, and leaves
ADR-0012's ergonomics reachable. Record the threat analysis ADR-0012 lacks,
including the path-read decision, as an amendment.

### D2 — loop-budget ceiling

`core/agent.py:36` declares `max_steps` as `ge=1` with no upper bound, and
`AgentRequest(max_steps=10**9)` is accepted **[run]**. No spec or ADR anywhere
discusses a ceiling, so unlike D1 this is a genuine gap rather than a closed
decision. But three things make revision 1's proposed fix wrong:

1. **The proposed clamp inverts a recorded decision.** Revision 1 said "clamp
   against `loop_settings.max_steps` as a server-side ceiling, not merely a
   fallback". `specs/0026` records the opposite precedence deliberately: "a
   caller who explicitly set `request.max_steps` or the kwarg has stated intent
   that beats the deployment default."
2. **`le=` on the DTO has no review record.** Per S6 the snapshot cannot see it,
   so revision 1's claim that "one regeneration is the review record" was false —
   there would be nothing to regenerate.
3. **`le=` plus a clamp is uncoverable.** If the DTO rejects the value, the clamp
   branch is unreachable; `core` carries a **100%** floor, so `make coverage`
   would fail. And a literal frozen into a protected DTO is unreachable by
   `Settings`, which the no-hard-coded-values rule forbids. Revision 1's migration
   note ("raise `MANGOMAS_LOOP__MAX_STEPS`") also does not work, because a
   pydantic `Field(le=...)` is resolved at class-definition time.

**Recommended resolution:** leave `core/agent.py` untouched. Add
`LoopSettings.max_steps_ceiling` and enforce it server-side in
`_effective_max_steps`, preserving spec-0026's precedence while bounding the
absolute value. This removes a protected-path touch, keeps the ceiling
operator-raisable, and is testable in both directions.

## Config / env additions

| Env var | Default | Purpose |
|---|---|---|
| `MANGOMAS_WORKFLOW__ALLOW_INLINE_DEFINITION` | `false` | D1: permit a per-request graph when the feature is disabled |
| `MANGOMAS_LOOP__MAX_STEPS_CEILING` | _(to be chosen)_ | D2: absolute server-side cap on loop steps |
| `MANGOMAS_WORKFLOW__MAX_DEPTH` / `__MAX_NODES` | _(to be chosen)_ | R1: graph bounds |

Each follows the existing rule — a `DEFAULT_*` module constant surfaced through a
`Settings` group, documented in the same commit in `config/`, `.env.example` and
`CLAUDE.md`, so `test_env_example_contract.py` stays green both directions. R3–R8
add no env var. Nothing is removed; retiring an inert setting is a separate
reviewed act (see `MANGOMAS_RAG__MIN_CHUNK_WORDS`, retired at `f8d37a1`).

## Protocol / contract impact

- **New/changed protocols:** none.
- **New error types:** none. R1's bounds reuse `ConfigError` (400).
- **Registry additions:** none.
- **Protected paths — `core/orchestrator.py` only.** With D2 resolved as
  recommended, `core/agent.py` is untouched. `core/orchestrator.py` is edited four
  times and each commit needs a `BREAKING-CHANGE` trailer: the D2 ceiling in
  `_effective_max_steps`, R6's seam extraction, and two R2′ items — the span held
  across a `yield` in `_stream_agent` (`:673-721`) and sibling cancellation in
  `dispatch_fan_out` (`:536-539`).
- **`errors.py` is a second protected path touched.** S14's fix defines `__str__`
  on `MangomasError`. That is a one-line change to a protected file that alters a
  published wire value, so it needs the trailer and its own note in the ADR.
- **Wire contract:** unchanged, and S6 makes that claim checkable for the first
  time.

**ADRs required — four, not one.** Revision 1 scoped a single ADR to cover what
are separate boundary decisions; one ADR saying four things cannot be reviewed as
one decision.

| ADR | Subject | Relationship |
|---|---|---|
| ADR-0030 | Workflow graph bounds + the `allow_inline_definition` seam | amends ADR-0012 with the threat analysis it lacks |
| ADR-0031 | Loop-budget ceiling | amends ADR-0026's precedence chain |
| ADR-0032 | Protected-path glob matching + the `core` seam | amends ADR-0021 |
| ADR-0033 | Default and reference posture (`GET /agents`, docs endpoints, manifest) | amends ADR-0014 |

Two R8 items each need their own decision before landing and are not covered by
the four above: retiring `harness/governance.py` (touches ADR-0021's deliberate
three-copy arrangement) and resolving the `parse_or_recover` contradiction
(spans `core/structured.py:117-118` and `specs/0015:203-205`).

## Backwards-compatibility

- No symbol is removed from a public surface; no import-level migration.
- R6 extractions leave a permanent re-export facade per ADR-0019, registered in
  `_FACADES` in the same commit — which S8's corrected test then enforces.
- `EvalReport` is a persisted artifact schema read back by `eval/baseline.py`;
  any move relocates the class, never its field set.
- The `combine-as-imports` change is formatting only. Verified twice: on
  `config/__init__.py` all 128 import bindings and 127 `__all__` entries are
  identical before and after (533 → 305 lines); repo-wide, enabling it reports 26
  findings, **all `I001` and all auto-fixable**, with no new rule family.
- **R1 and R2 change behaviour by intent.** Requests that succeed today must stop:
  a deeply nested graph, an unauthenticated roster read, a docs fetch outside
  `local`. Each goes in `CHANGELOG.md` under `Changed` with its migration, not
  under `Fixed`.

## Test plan

- **Unit:** `tests/test_workflow_api.py` (**not** `test_workflow_http.py`, which
  does not exist — a revision 1 error) and `tests/test_api.py` for R1/R2;
  `tests/deploy/` for R4/R5; `tests/test_import_compat.py` and
  `tests/test_openapi_snapshot.py` for R3/R6; `tests/tooling/` for the parity
  pins; `test_env_example_contract.py` for R7.
- **Mutation proof is mandatory and named per guard.** Six R3 milestones are
  ratchets over properties already held, so "failing test first" is impossible —
  the test passes on arrival. `specs/TEMPLATE.md:26-30` is explicit that a guard
  which cannot be shown to fire is the defect. Each carries its named mutation:

| Guard | Mutation that must turn it red |
|---|---|
| S6 snapshot | add `le=` to a scratch DTO field |
| S7 governance | convert a scratch copy of a protected module to a package |
| S8 facades | add a scratch re-export package absent from `_FACADES` |
| S9 lockstep | bump `ruff==` on one side only |
| S10 nightly | add `continue-on-error` to a job in `needs:` |
| coverage floors | delete one test and confirm the *specific* floor goes red |

- **Coverage floors:** set per row with a stated reason, **not** by a blanket
  rule. Revision 1 claimed `measured − 2` was "this repo's convention"; there are
  two precedents pointing opposite ways, and citing one without the other was an
  inference presented as fact. `Makefile:36-39` uses a two-point margin for
  `SCRIPTS_FLOOR`; `scripts/check_coverage.py:66-69` records the opposite for
  `_entry_points` — "already at 100%, so the floor is set where the code actually
  is rather than below it" — and eight current floors sit at 100/100 with no
  margin. Use a margin only where a cloud SDK or subprocess boundary makes the
  number jitter (`adapters`, `scripts`); set the rest at measured.
- **Gated suites:** untouched. Nothing here needs LM Studio, Vertex, Postgres or
  a live GCP project.

## Acceptance criteria

- [ ] `make gate` green at every landing, not merely at the end.
- [ ] Every scenario S1–S12 has a test that fails before the fix and passes after,
      with the mutation from the table above actually performed and recorded.
- [ ] A 16 KB nested graph raises `ConfigError`, not `RecursionError`.
- [ ] With `fail_fast` set and an awaiting fake agent, exactly `len(rows) - 1`
      rows are skipped — asserted as an exact count, not `assert cancelled`.
- [ ] A 404 body's `message` carries no embedded quoting, asserted directly;
      `tests/test_metrics.py:433,438` are deleted or rewritten to be able to fail.
- [ ] `MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS` reaches pool configuration, and a
      CLI dispatch with metrics enabled installs a real `MeterProvider`.
- [ ] A RAG re-ingest that fails after the delete leaves the prior vectors intact.
- [ ] With auth enabled, `GET /agents` returns 401; `/docs`, `/redoc` and
      `/openapi.json` are unreachable when `MANGOMAS_ENV != "local"`.
- [ ] `deploy/service.yaml` sets auth and both backpressure knobs, asserted by a
      test; `deploy/README.md` documents the required set.
- [ ] `/readyz` honours `ready_timeout_seconds`, wired via `app.state` rather than
      a `get_settings()` call inside `health.py`.
- [ ] The snapshot projection includes per-property validation keywords, proven
      by the S6 mutation.
- [ ] No per-package coverage floor is below its measured value minus its stated,
      per-row margin.
- [ ] Every R6 extraction is registered in `_FACADES` and covered by the corrected
      S8 test.
- [ ] `CHANGELOG.md` `[Unreleased]` names the test for each landed workstream;
      R1/R2 behavioural changes sit under `Changed` with their migration.
- [ ] ADR-0030 through ADR-0033 recorded before their workstreams land;
      `BREAKING-CHANGE` trailer on every `core/orchestrator.py` commit.
- [ ] D1 and D2 are resolved by a written decision before any code implementing
      them is merged.

## Review record

Revision 1 was adversarially peer-reviewed and came back **request changes**.
Corrections carried into revision 2, recorded so the errors are not repeated:

1. **"Unauthenticated cluster" was false.** Both workflow routes carry
   `Depends(require_auth)`. Withdrawn and replaced with the measured auth map.
2. **D1 re-opened a rejected alternative.** ADR-0012 explicitly rejected
   "conditionally mount routes when enabled"; neither revision-1 document named
   ADR-0012, and three tests pin the current behaviour.
3. **The snapshot cannot see `le=`.** Revision 1's "one regeneration is the review
   record" was false. This became S6, a new finding.
4. **`le=` plus a clamp is uncoverable** under `core`'s 100% floor, and the stated
   migration path does not work.
5. **Collapsing the two precedence chains would reintroduce a fixed bug.**
   ADR-0027 and the `_pipeline_effective_max_steps` docstring both record that
   `request.max_steps` is *deliberately* excluded from the pipeline chain to avoid
   double-applying. Revision 1 called them "near-identical".
6. **The streaming cap contradicted ADR-0025**, whose recorded invariant is that
   "a persisted streamed turn is always the complete answer the client received".
   Moved out of scope pending an ADR amendment.
7. **The facade completeness test could not police any proposed extraction.**
   Respecified in the other direction (S8).
8. **`C901` at 15 was an anti-ratchet**, and the reviewer's proposed 10 fails too.
   Measured on the full lint surface: 13 is the tightest passing threshold (two
   `tests/` functions at complexity 12 and 13 bind it); `src/` alone passes at 10.
9. **`measured − 2` is not this repo's convention** — there is a recorded
   counter-precedent in the file the ratchet edits.
10. **`NEXT_STEPS.md:581` defers `RET`/`PERF`/`C90`**, and revision 1 quoted that
    line with `C90` silently removed while proposing to adopt it.
11. **`tests/test_workflow_http.py` does not exist**; cited three times.
12. **`health.py` uses `[:DEFAULT_ERROR_DETAIL_TRUNCATE]`**, not a bare `[:200]`,
    with a comment giving the reasoning revision 1 said was absent.
13. **The dead `--check-protected-paths` flag is not a new finding** — already
    recorded at `docs/analysis/20260822-ssd-template-pack-analysis.md:71`.

One reviewer objection was **not accepted**: that ratcheting the `adapters` floor
to 93 depends on the Vertex extraction landing first. Measured — `adapters` is at
95% today and a 93 floor passes with exit 0 independently. The extraction raises
the reachable ceiling; it is not a prerequisite.
