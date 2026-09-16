# Code quality, tech-debt and enterprise readiness — delivery plan

- **Branch:** `claude/code-quality-tech-debt-plan-dag2y8`
- **Date:** 2026-09-16 (revision 2 — peer-reviewed)
- **Target release:** rolling → `v0.5.0`
- **Status:** Draft
- **Specs:** spec-0031
- **ADRs:** ADR-0030 … ADR-0033, one per boundary. See spec-0031.

## Executive summary

The reflection began by running the gate rather than reading about it. **All
twelve `make gate` steps pass on a clean tree at `df92e3d`** — 2683 tests in 51s,
98.88% coverage against a 95% floor, `mypy --strict` clean over 433 files. CI is
green and this is not a remediation program.

**Revision 1 of this plan was wrong about its own headline and has been
rewritten.** It claimed an "unauthenticated HTTP cluster"; both workflow routes
in fact carry `Depends(require_auth)`, and the behaviour it called a defect is a
ticked acceptance criterion in spec-0008 backed by an ADR that explicitly
rejected the remedy proposed. Three of its fixes would have reintroduced bugs
that existing ADRs were written to prevent. The full correction list is in
spec-0031's "Review record"; the sequencing below is rebuilt from it.

What survives is stronger for being narrower:

- **One unambiguous bug**, with no decision behind it and no protected path: a
  16 KB request body makes `RecursionError` escape the `ConfigError` boundary,
  and the code comment asserting this is impossible is false. It leads.
- **A default posture that the reference manifest does not correct**, plus four
  routes outside the auth seam — one of them, `GET /agents`, unexplained.
- **Six guards that cannot fire**, including a new one found by peer-reviewing
  revision 1: the wire-contract snapshot is structurally blind to constraint
  narrowing on a published DTO.
- **Slack**: coverage floors below what the code holds, and ~1,000 lines of
  import-formatting noise removable by one setting.
- **Two open decisions** that are not defects and are carried as questions.

Sequencing runs settled-fact-first: the bug, then posture, then the guards, then
everything the guards make safe. The `orchestrator.py` split is last, because
converting it to a package would silently disable the protected-path gate.

## Measured baseline (2026-09-16, `df92e3d`)

| Gate step | Result |
|---|---|
| `validate-config`, `lint`, `format-check` | pass — 433 files formatted, 0 findings |
| `typecheck` (`mypy --strict`) | pass — 433 files, 0 issues |
| `lint-imports` | pass — 180 files, 477 deps, 2 contracts kept |
| `frontmatter` | pass — 17 skills, 27 agents |
| `protected-paths` | pass |
| `test` | 2683 passed, 66 skipped, 2 warnings, 51.5s |
| `coverage` | all 21 floors met; global 98.88% |
| `bridge` / `contracts` / `scripts` coverage | 100% / 100% / 96% |

16,326 source lines against 35,740 test lines (2.19×). 496 functions, 25 over 50
lines, 6 over 80. Churn concentrates in the **governance meta-layer**, not the
product: across 267 commits the most-edited files are `tests/constants.py` (56),
`pyproject.toml` (32), `config.py` (21), `test_ci_make_parity.py` (19),
`Makefile` (19), `ci.yml` (16). The tooling that enforces quality costs more
maintenance than the code it enforces — which is why PR C prefers a generalising
matcher over more hand-maintained rows.

**Verified clean, recorded so it is not re-audited:** zero TODO/FIXME markers,
zero commented-out code, zero unused imports, every SQL query parameterised,
every outbound client carries a configured timeout except Vertex, no broad
`except` swallows cancellation, and the auth seam correctly returns 401 on every
route that declares it.

## PR A — Bound the workflow graph (spec-0031 R1)

One milestone. The only finding in the audit with no closed decision behind it,
no protected path, and no ADR to supersede.

### Milestone A1 — depth, node-count and source bounds

- **Failing test first:** `load_workflow` on a deeply nested graph asserts
  `ConfigError`. Red today — measured: depth 500 (16 KB) → `RecursionError`;
  depth 200 validates cleanly.
- **Depends on:** nothing.
- `workflow/graph.py:81-86` comments that "v1 keeps nesting bounded to depth two,
  so no recursion is possible". `WorkflowStep` includes `FanOutNode` and
  `BranchNode`, so both recurse without limit and the comment is false. Fix the
  comment in the same commit.
- `RecursionError` derives from `RuntimeError`, so `except json.JSONDecodeError`
  misses it and it surfaces as a 500 through the access-log middleware.
- Add a depth counter and node-count cap before `model_validate`, a byte cap on
  `source`, `max_length` on `branches`/`steps`, and catch `RecursionError` in
  `load_workflow`. Bound the unbounded `asyncio.gather` at
  `workflow/nodes/fan_out.py:54-56` while here.
- **ADR-0030** covers the bounds together with D1's seam.

## PR B — Default and reference posture (spec-0031 R2)

### Milestone B1 — `GET /agents` joins the auth seam

- **Failing test first:** with auth enabled and no credential, assert 401. Red
  today — returns 200 and lists every registered agent.
- **Depends on:** nothing.
- `api/routes/system.py`'s module docstring justifies the probe exemption
  explicitly under ADR-0014 and says nothing about the roster route, which sits
  in the same router and appears to have inherited the exemption by placement
  rather than by decision.

### Milestone B2 — the API schema is not public outside `local`

- **Failing test first:** assert `/docs`, `/redoc`, `/openapi.json` are
  unreachable when `MANGOMAS_ENV != "local"`. Red today — all four FastAPI
  defaults serve 200 regardless of auth, because `create_app` never passes
  `docs_url`/`redoc_url`/`openapi_url`.
- **Depends on:** nothing.

### Milestone B3 — the readiness probe honours its own budget

- **Failing test first:** drive a slow fake past the budget and assert `/readyz`
  reports failure within it. Red today — no `asyncio.timeout` anywhere in
  `health.py`, so the effective budget is `llm.timeout_seconds` (60.0s), 30× the
  documented 2.0s. No test references the field at all.
- **Depends on:** nothing.
- **Wiring matters here.** `check_ready(orchestrator)` takes only an orchestrator
  and never calls `get_settings()`. Reaching for settings inside `health.py`
  would put a service locator in a helper module, against the composition-root
  rule. Follow the existing precedent: `api/app.py:177` already does
  `app.state.auth = resolve_auth_state(_settings)`, so store the resolved timeout
  on `app.state` the same way and pass it from the route.

### Milestone B4 — the reference manifest is safe to copy

- **Failing test first:** `tests/deploy/` asserts `deploy/service.yaml` sets auth
  and both backpressure knobs. Red today.
- **Depends on:** nothing.
- The manifest sets `MANGOMAS_ENV=prod`, telemetry, provider and secrets vars and
  **none** of `AUTH__ENABLED`, `AUTH__SECRET_REF`, `API__MAX_BODY_BYTES`,
  `API__MAX_CONCURRENT_REQUESTS`. With `containerConcurrency: 80` an operator
  following it verbatim publishes an unauthenticated LLM proxy, and
  `deploy/README.md` mentions only CORS. Add an ingress annotation and document
  the required set.
- **ADR-0033** records this as an amendment to ADR-0014's default posture.

### Milestone B5 — isolated correctness fixes

- **Depends on:** nothing. Each is a few lines with its own regression test.
- `auth.py:105` — `secrets.compare_digest` raises `TypeError` on non-ASCII, so
  `Authorization: Bearer tökén` returns 500 instead of 401 and a non-ASCII
  configured token 500s every request. Compare encoded bytes. The comparison is
  otherwise correctly constant-time and fails closed.
- `adapters/llm/vertex.py:120,127` — `self._timeout_seconds` is assigned and
  never read; the three `generate_content_async` calls are unbounded. This is the
  only unbounded outbound call in the repo.
- `rag/retrieval.py:154-156` — `top_k` arrives from the model's tool-call JSON
  with a `max(1, …)` floor and no ceiling, so retrieved content can drive an
  enormous query. Clamp it.
- `adapters/storage/postgres.py:165,210` — `await self._ensure_pool()` sits
  *outside* the `try` in both methods, so an asyncpg auth failure propagates
  unwrapped and `check_ready`'s broad `except` copies e.g. `password
  authentication failed for user "…"` into the public body. Move it inside.
- `adapters/storage/memory.py:38-42` — `f"{prefix}-{today}.md"` is unsanitised,
  so a `../` prefix escapes the memory dir. Latent (no caller passes one) but the
  protocol exposes it.
- **Not in this milestone:** the streaming accumulation cap. Revision 1 filed it
  here; it edits a protected path and its proposed fix ("persist a truncation
  marker") inverts ADR-0025's recorded invariant that "a persisted streamed turn
  is always the complete answer the client received". See Deferred.

### Milestone B6 — secret containment

- **Failing test first:** `assert "s3cret" not in repr(Settings(...))`.
- **Depends on:** nothing.
- `SecretStr` appears nowhere in `src/`. `LLMSettings.api_key` is a plain `str`
  that receives the resolved secret — for Vertex that is the **service-account
  JSON body** (`composition/llm.py:61`) — and `DBSettings.url` carries a DSN with
  a password. No current call site logs a settings object, so this is a latent
  footgun rather than an active leak, but nothing prevents one.

## PR C — Gate integrity (spec-0031 R3)

Six guards that cannot currently fire. Every one is a ratchet over a property
already held, so "failing test first" is impossible — **each therefore carries a
named mutation that must turn it red**, per `specs/TEMPLATE.md:26-30`. Revision 1
asserted these guards would work without demonstrating they could fail, which is
the exact defect the plan is about.

### Milestone C1 — the snapshot must see a narrowed constraint

- **Mutation:** add `le=` to a scratch DTO field; assert red.
- **Depends on:** nothing. **Blocks any future DTO constraint change.**
- This is a **new finding, produced by peer-reviewing revision 1**, and it is the
  most valuable item in PR C. `tests/test_openapi_snapshot.py:46-62` pins a
  projection of property **names** and **required** sets only. Measured by adding
  `le=100` to `AgentRequest.max_steps` in memory and rebuilding:

```
raw schema:  {'type': 'integer', 'maximum': 100.0, 'minimum': 1.0, ...}
projection changed?  NO
```

- The snapshot's docstring says it exists because "nothing mechanical noticed a
  DTO field turning required, a route disappearing, or a schema being reshaped".
  Constraint narrowing is a fourth case it never covered, so any PR tightening
  `le`/`ge`/`max_length`/`enum` on a published DTO ships with no diff and no
  review record. Extend `_projection()` to include per-property validation
  keywords.

### Milestone C2 — protected-path governance survives a package conversion

- **Mutation:** convert a **scratch copy** — never the real module — to a package
  and assert the gate exits non-zero.
- **Depends on:** nothing. **Blocks F5.**
- `scripts/check_protected_paths.py:128` computes `set(changed) &
  protected_paths`, an exact string intersection, so `core/orchestrator/…`
  matches nothing and the gate prints "No protected core contracts changed. OK."
- **Use glob matching, not more rows.** Replace the four `core/*.py` entries with
  `src/mangomas/core/**/*.py`. That closes the package-conversion hole, the
  new-sibling hole, and the currently-unprotected `core/__init__.py` (the actual
  public facade) and `core/loop.py`, in one change — and it stops the table
  growing by a row per future split, which matters given the churn profile above.
  Mirror it in `lint_agent_frontmatter.py`'s two matchers, which use the same
  literals.

### Milestone C3 — the facade registry notices an *unregistered* facade

- **Mutation:** add a scratch re-export package absent from `_FACADES`; assert
  red.
- **Depends on:** nothing.
- **Revision 1 specified this test backwards.** It checked that a *registered*
  package's submodules are all listed — but every extraction PR F proposes lands
  in an **unregistered** package: `adapters.llm`, `cognitive`, `core`, `scripts`,
  `tests.constants`. `mangomas.core` is absent from `_FACADES` entirely, which
  means the `tools.py` → `structured.py` precedent PR F cites as its model was
  never registered either. Walk `src/mangomas/**/__init__.py` instead and flag any
  package that re-exports names it does not define while being absent from
  `_FACADES`.

### Milestone C4 — pre-commit revisions match the pyproject pins

- **Mutation:** bump `ruff==` on one side only; assert red.
- Both files carry a "keep in lockstep" comment and none of
  `test_precommit_parity.py`'s four tests checks it, so the hook can silently run
  a different linter than CI — the "green locally, red in CI" class. The
  identical duplication for the coverage floor *is* guarded at
  `test_ci_make_parity.py:226`. While here: `.pre-commit-config.yaml:36` declares
  `starlette>=0.40` in `additional_dependencies`, and it is not a declared project
  dependency at all.

### Milestone C5 — the nightly reporter can report

- **Mutation:** add `continue-on-error` to a job in `needs:`; assert red.
- `nightly.yml:127` marks `sbom-scan` `continue-on-error: true` while listing it
  in `notify`'s `needs:`, so it can never reach a failed conclusion and
  `if: failure()` never fires for it. The existing guard asserts `needs:`
  membership, not that the job can fail. Keep the scanner non-blocking via
  `trivy --exit-code 0`; drop `continue-on-error` so a *crashed* scan reports.

### Milestone C6 — coverage floor ratchets, per row with a reason

- **Mutation:** delete one test and confirm the **specific** floor goes red. A
  floor set below current passes whether or not it moved, so `make coverage` is
  not a proof.
- **Depends on:** nothing. Verified: `adapters` is at 95% today and a 93 floor
  passes with exit 0 **independently of the Vertex extraction** — the extraction
  raises the reachable ceiling, it is not a prerequisite.
- **Drop revision 1's blanket rule.** It claimed `measured − 2` is "this repo's
  convention". There are two precedents pointing opposite ways:
  `Makefile:36-39` uses a two-point margin for `SCRIPTS_FLOOR`, while
  `scripts/check_coverage.py:66-69` records the opposite for `_entry_points` —
  "already at 100%, so the floor is set where the code actually is rather than
  below it" — and eight current floors sit at 100/100 with no margin. Use a
  margin only where an SDK or subprocess boundary makes the number jitter.

| Floor | Measured | Current | Proposed | Reason |
|---|---|---|---|---|
| adapters | 95 | 85 | 93 | margin — lazy cloud SDKs are unreachable in CI |
| cli | 100 | 95 | 100 | no boundary; set at measured |
| config | 100 | 95 | 100 | no boundary |
| workflow | 100 | 95 | 100 | no boundary |
| harness | 100 | 95 | 100 | no boundary |
| api | 99 | 95 | 99 | no boundary |
| eval | 99 | 95 | 99 | no boundary |
| composition | 99 | 95 | 99 | no boundary |
| agents | 99 | 95 | 99 | no boundary |
| telemetry | 99 | 95 | 99 | no boundary |
| global | 99 | 95 | 97 | margin — aggregate moves with every PR |

`metrics`, `rag`, `cognitive` measure 97 and stay at 95 pending a per-row reason.
The global floor also lives in `pyproject.toml`'s `--cov-fail-under`; both move
in one commit, already enforced at `test_ci_make_parity.py:226`.

**Every row above was run, not assumed.** All 22 proposed floors pass against the
current coverage data. The mechanism was separately shown able to fire: a floor
one point above measured exits non-zero (`adapters` at 96 → exit 2, `cli` at 101
→ exit 1), so a passing row means the code holds the level rather than the gate
being inert.

### Milestone C7 — complexity and warning ceilings

- **Mutation:** a scratch function above the threshold; a scratch
  `DeprecationWarning`.
- **Both thresholds were wrong in revision 1 and are now measured.**
  Revision 1 proposed `max-complexity = 15`, which is *looser* than the property
  held — an anti-ratchet. Peer review proposed 10, which fails. Measured across
  the full `make lint` surface:

| `max-complexity` | Violations |
|---|---|
| 10 | 2 |
| 12 | 1 |
| 13 | **0** |
| 15 | 0 |

- The two binding functions are in `tests/` (`pytest_collection_modifyitems` at
  12, `test_concurrent_register_and_get_does_not_corrupt_store` at 13). `src/`
  alone passes at 10. Adopt **13 repo-wide**, or 10 with a `tests/` per-file
  ignore — the second locks the property where it matters.
- **Cite the deferral honestly.** `NEXT_STEPS.md:581` defers
  `RET`/`PERF`/**`C90`**. Revision 1 quoted that line with `C90` silently removed
  while proposing to adopt it. Say plainly that this re-opens `C90` because
  measurement now shows the property is held, and drop `C90` from the deferred
  list in the same commit so the two records cannot disagree.
- **`filterwarnings` is validated, not asserted.** Bare `-W error` breaks
  collection in 12 files. With the two upstream deprecations ignored by name the
  full suite passes: 2683 passed, 66 skipped. Use exactly:

```
filterwarnings = [
    "error",
    "ignore::starlette.exceptions.StarletteDeprecationWarning",
    "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning",
]
```

### Milestone C8 — two coverage-shaped gaps

- `MAX_TENANT_ID_LENGTH` (`tenancy.py:40`) has **no truncation test**, despite
  `tenancy.py` sitting in a 100%-floor group; its twin `MAX_CORRELATION_ID_LENGTH`
  is asserted in two files. A security-relevant clamp reporting 100% with no pin
  is what `mango-coverage-audit` exists to catch.
- Five `SIGNAL_*_ENV` constants are defined, exported and unused, while their
  three siblings are used — a missing env-override suite on the signal settings
  rather than surplus constants.

## PR D — CI/CD economics and release integrity (spec-0031 R4)

### Milestone D1 — concurrency groups

- **Failing test first:** `test_every_workflow_declares_a_concurrency_group`. Red
  on all four.
- `ci.yml` triggers on `push: ["**"]` *and* `pull_request`, so **every push to an
  open PR branch runs all eight jobs twice** and stacked pushes never cancel.
  Use `cancel-in-progress: true`, except `deploy.yml` where cancelling
  mid-rollout is worse than queueing. Largest cost win available.

### Milestone D2 — job timeouts

- **Failing test first:** `test_every_job_declares_a_timeout`. Red everywhere —
  no job sets `timeout-minutes`, so the default is six hours. `eval-gate.yml`'s
  background uvicorn and `deploy.yml`'s smoke loop are the realistic hangs.

### Milestone D3 — the deploy gate must be the gate

- **Failing test first:** `test_deploy_verify_runs_the_full_gate`. Red today.
- `deploy.yml`'s `verify` runs `make test`, `make coverage` and
  `make contracts-coverage` — three of twelve steps, omitting `lint`,
  `format-check`, `typecheck`, `lint-imports`, `frontmatter`, `protected-paths`,
  `bridge-coverage` and `scripts-coverage`. **A release can deploy code that
  fails type-checking.** Replace the three with `make gate`.

### Milestone D4 — cache the analysers, collapse redundant installs

- **Depends on:** D1.
- `make install` runs seven times per `ci.yml` run and nothing caches
  `.mypy_cache`, `.ruff_cache` or `.import_linter_cache`, so `mypy --strict`
  starts cold every time (warm locally: 1s). Cache all three on `lint`, and merge
  the three isolated coverage jobs into one three-step job — that preserves the
  per-step failure attribution which is the stated reason they are separate,
  while removing two full installs.

### Milestone D5 — required-checks and template drift

- **Failing test first:** `test_contributing_required_checks_match_ci_job_names`
  — passes today (9 documented names, 8 jobs, exact match), so it lands as a
  ratchet with a rename mutation. Branch protection matches on check *name*.
- The checks are documented but **not yet actually required** on
  `feat/initial-release`, recorded in four places as decision D1 of the
  2026-08-22 roadmap, "the cheapest unblock in the register". Until an admin
  flips it, a trailer-less protected-path PR shows red and merges anyway.
- `.github/PULL_REQUEST_TEMPLATE.md:17-23` lists raw commands over
  `src tests scripts` rather than `make` targets over the wider `CODE_PATHS`.

## PR E — Dependency determinism (spec-0031 R5)

### Milestone E1 — constrain the CI install

- `requirements.lock` pins the transitive runtime closure but is consumed **only
  by `Dockerfile:47`**. CI runs `pip install -e ".[dev]"` unconstrained, so a
  green pipeline can turn red overnight from an upstream release with no local
  change — `pytest` resolved to 9.1.1 here against a `>=8.2` floor. Extend the
  lockfile to the dev extra and pass it as constraints from `make install`, or
  pin the remaining floors. The four analysers are already exact-pinned for
  precisely this reason.

### Milestone E2 — declare or guard `mango_contracts`

- Imported **unconditionally at module top level** in seven places under
  `cognitive/`, and present in no dependency list and no extra. Reachable only
  via a manual editable install, pytest's `pythonpath`, or the Dockerfile's
  second wheel. This is deliberate and documented (keeping `requirements.lock` an
  `==`-pin of PyPI names) and the flag-**off** path is genuinely safe. But the
  flag-**on** path in a plain `pip install mangomas` raises a bare `ImportError`
  rather than a typed `ConfigError` with an install hint.
  `eval/_langfuse.py:32-37` already does the right thing for its optional SDK.

### Milestone E3 — Dependabot and extras coverage

- Add the `docker` ecosystem so the digest-pinned base image stops rotting by
  hand, and `groups:` so an action bump is one PR rather than N.
- `pip-audit` audits the installed env, which is `.[dev]`, so the
  `google-cloud-aiplatform`, `chromadb`, `sentence-transformers` and `langfuse`
  trees are **never scanned**. Add a second audit over the cloud/rag extras.
- Nothing verifies `requirements.lock` still matches `pyproject.toml`.

## PR F — Oversized-module reduction (spec-0031 R6)

### Milestone F0 — `combine-as-imports` (standalone, land any time)

- **Depends on: nothing.** Revision 1 filed this under module reduction and gated
  it behind the facade guard. It is neither: the milestone body itself concedes
  `config/__init__.py` "has zero functions and zero classes — it is not a god
  file", and its proof is `test_import_compat.py` staying green, which exists
  today. Highest value/risk ratio in the plan; do not gate it.
- **Measured twice.** On `config/__init__.py`: 533 → 305 lines with all 128 import
  bindings and 127 `__all__` entries byte-identical. Repo-wide: enabling the
  setting reports 26 findings, **all `I001`, all auto-fixable, no new rule
  family**. 546 aliased import statements across 9 facade files, the largest
  being `tests/constants/__init__.py` (277 aliases, 626 lines).
- Check before landing that no module newly needs a `PLC0414` exemption; today
  only `cli/main.py` and `core/tools.py` carry one.

### Milestone F1 — `adapters/llm/vertex.py` (429 → ~230)

- **Depends on:** C3.
- Extract `_vertex_sdk.py` (lazy bootstrap + credentials, all `pragma: no cover`)
  and `_vertex_wire.py` (pure role/content mappers) along the three banner-marked
  clusters that already exist. The payoff is that the `adapters` floor sits low
  *because* SDK-bound code is unreachable in CI; isolating it lets the pure
  mappers be measured at 100% and raises the reachable ceiling above C6's 93.

### Milestone F2 — `cognitive/producer.py` (285 → ~130)

- **Depends on:** C3.
- Extract `_payloads.py` (pure truncation/shaping) and `_lineage.py` (trace id,
  event ids, digests). Constraints: the module keeps importing nothing from
  `mangomas.agents`, and `_field_max_length` runs at import time reading Pydantic
  metadata, so that binding stays with whichever module owns truncation.

### Milestone F3 — `scripts/lint_agent_frontmatter.py` (847, largest file)

- **Depends on:** C3.
- Four independent, already-banner-separated modes; `scripts/_governance.py` and
  `scripts/_stdin_json.py` are the precedent. **Hard constraint:** hook modes must
  stay importable with neither `pydantic` nor `pyyaml` present, so any module on
  the hook path is stdlib-only. Update `Makefile:24`'s `SCRIPTS_TESTS` in the same
  commit — it hard-codes test paths, and a missed entry drops tests out of the
  `scripts-coverage` gate silently.

### Milestone F4 — `tests/constants/corpus.py` (712, pure data)

- 89 constants under six existing banner sections, zero logic. Split behind the
  `tests.constants` facade. Justified by churn: its lineage is the most-edited
  file in repository history at 56 commits.

### Milestone F5 — `core/orchestrator.py` (721 → ~600) ⚠ highest risk

- **Depends on: C2. Do not start before C2 lands.**
- **Do not convert it to a package.** `core/` has a module-facade precedent
  (`tools.py` → `structured.py`), not a package one.
- **Revision 1 picked the wrong seam.** It proposed extracting the acceptance
  loop, calling `_effective_max_steps` and `_pipeline_effective_max_steps` "two
  near-identical precedence chains". They differ **by design**: ADR-0027 records
  that "`request.max_steps` is excluded because it already governs each inner
  dispatch and would otherwise double-apply", and the
  `_pipeline_effective_max_steps` docstring repeats it. Collapsing them
  reintroduces the bug ADR-0027 was written to prevent. Revision 1 also inflated
  the duplication: the genuinely shared portion of `_dispatch_once` and
  `dispatch_pipeline` is the ~16-line accept/re-inject/`MaxStepsExceeded` block,
  and extracting it means passing an async callback across a module boundary in
  the file this repo protects most.
- **Take the fan-out cluster instead.** `dispatch_fan_out`,
  `dispatch_fan_out_settled`, the `FanOutOutcome` dataclass and
  `_outcome_error_code` form a cohesive ~110-line group with no shared mutable
  state, no acceptance semantics and no precedence chain — and one already-public
  export (`from mangomas.core.orchestrator import FanOutOutcome`) whose
  preservation is easy to prove. That is what `core/_topology.py` should hold.
- With C2's glob matcher in place, **no governance-table change is needed** —
  only the `BREAKING-CHANGE` trailer. ADR-0032 covers the matcher and the seam.

## PR G — Inert and hard-coded configuration (spec-0031 R7)

The `DEFAULT_*` discipline in `src/` is strong — no unexplained magic number in
application code, and the two most suspicious duplicates turned out to be
deliberate and mechanically pinned. The defects are at the edges.

### Milestone G1 — `.env.example` value contract

- **Failing test first:** compare each `KEY=value` against
  `model_fields[...].default` with an allowlist for illustrative blocks. Red on
  four lines: `LOG__BODY_TRUNCATE` (2000 vs 512), `API__READY_TIMEOUT_SECONDS`
  (5.0 vs 2.0), `API__HISTORY_DEFAULT_LIMIT` (50 vs 10), `API__HISTORY_MAX_LIMIT`
  (500 vs 1000). All four are commented, and the file header says commented
  blocks are opt-in features — but sibling commented lines in the same blocks
  restate the real default, so nothing tells a reader which kind of line they are
  looking at.

### Milestone G2 — every documented setting has a consumer

- **Failing test first:** red on three today — `api.ready_timeout_seconds`,
  `api.host`, `api.port` — plus `log.body_truncate`, the same shape.
- **Depends on:** B3 wires the first of them.
- For `host`/`port`, wire them to the serving bootstrap or retire them the way
  `MANGOMAS_RAG__MIN_CHUNK_WORDS` was retired at `f8d37a1`. Leaving an
  **uncommented** `MANGOMAS_API__PORT=8000` beside the real `PORT` contract is the
  trap; `8000` appears 12 times across 6 files with nothing binding them.

### Milestone G3 — re-export restated test constants, name duplicated literals

- `tests/rag/test_chunker.py:18` (800/120), `tests/composition/helpers.py:80-81`
  (provider names), `tests/adapters/test_shared_errors.py:17` (base URL),
  `tests/integration/test_tenancy_stream_flow.py:35` (needs
  `DEFAULT_TENANCY_HEADER` re-exported first). Add a guard so the `X as X` rule
  stops being prose.
- `_SDK_INSTALL_HINT` is byte-identical in `adapters/llm/vertex.py:60-62` and
  `adapters/embeddings/vertex.py:27-29`; both already import from
  `adapters/_vertex_errors.py`, the obvious home. The `"source"` vector-metadata
  key is restated across `rag/pipeline.py`, `rag/retrieval.py` and
  `adapters/vector/chroma.py` — a genuine cross-layer wire contract that `rag/`
  cannot import from `adapters/`; `cognitive/constants.py` is the in-repo
  precedent for naming exactly this shape.
- Narrow `pyproject.toml:215`'s blanket `S603`/`S607` ignore for `scripts/*` to
  per-line `noqa` — its stated justification is about `T201`/`PTH` and does not
  cover the subprocess family. Reword `harness/governance.py:123-124`, whose
  comment claims the path is "not attacker-controlled" when it originates from
  hook stdin; the real defence is the fixed argv plus `--`.

## PR H — Dead code and enterprise organisation (spec-0031 R8)

Recorded first, because it is the larger truth: **zero TODO/FIXME markers, zero
commented-out code, zero unused imports** under the project's own config, every
declared dependency and extra resolving to a real import, and every
`tests/fakes.py` member used. The findings are surface that outlived its
consumer.

### Milestone H1 — retire the third copy of protected-path governance

- `src/mangomas/harness/governance.py` is 141 lines with **no production
  consumer** — its own docstring says so. It is a third implementation of logic in
  `scripts/_governance.py` and `scripts/lint_agent_frontmatter.py`, including a
  byte-identical marker regex, with the protected-path list stated in four
  places. The recorded justification (scripts must run without `mangomas`
  installed) covers the two `scripts/` copies, not the third, which exists so the
  logic lands inside `--cov=mangomas` — so the coverage number measures code that
  never executes. Needs its own decision: it touches ADR-0021's arrangement.

### Milestone H2 — correct four documents about a dead code path

- `--check-protected-paths` (~45 lines) has no invoker: not
  `.pre-commit-config.yaml` (which passes no flag), not the `Makefile`, not any
  workflow or hook. `CLAUDE.md:542-544`, the script docstring, `ADR-0021:57` and
  `spec-0017:97` all describe it as a live pre-commit hook.
- **Not a new finding.** Already recorded at
  `docs/analysis/20260822-ssd-template-pack-analysis.md:71`; revision 1 claimed
  novelty it does not have. Either wire it in or delete it and fix the documents.

### Milestone H3 — decide on the structured-output helpers

- `parse_or_recover` and `parse_llm_json_object` (both on a **protected path**)
  have zero production callers, and the design says otherwise:
  `core/structured.py:117-118` names the LLM-judge scorer as a consumer, and
  `spec-0015:203-205` states the planner/reviewer path adopted the helper. In fact
  `agents/_structured.py:96-121` calls `model_validate_json` directly, so the
  brace-span recovery these helpers provide — the documented failure mode of local
  models emitting fenced JSON — is **not reachable from planner or reviewer**, and
  `eval/scorers/llm_judge.py:80-92` hand-rolls 13 lines of the same logic.
- A human decision, not a cleanup, and it needs its own ADR: either give the
  helper a `message=` parameter and adopt it in both places, or delete it and drop
  the false claims from the docstring and the spec.

### Milestone H4 — collapse the remaining duplication

- `_FLAG_BY_NAME` + `_resolve_flags` are byte-identical in
  `eval/scorers/regex_match.py:26-40` and `workflow/predicate.py:30-60`. The
  layering constraint is real, but this repo already solved that shape twice by
  promoting shared mechanics to a neutral leaf (`_headers.py`,
  `_entry_points.py`).
- The discovery idempotency latch is duplicated in `agents/discovery.py` and
  `eval/discovery.py`. `_entry_points.py`'s docstring explains what stays
  caller-side and why — that reasoning covers the loop body, not the latch.
- Test-side: **ten separate `EvalReport` builders** (three byte-identical), the
  workflow helper quartet duplicated four times, eleven independent `_req`
  definitions. `tests/composition/helpers.py` establishes the fix pattern.

### Milestone H5 — small removals and organisation

- Two unused fixtures (`tests/conftest.py:212,226`); nine unused
  `tests/constants` members (five are C8's test gap). `#
  approved-breaking-change`: **0 occurrences in 267 commits** against 16 for
  `BREAKING-CHANGE`. `EvalRunner.run(agent_name=...)`: no production caller, kept
  alive by ~18 test call sites — compat surface, so it needs a decision.
  `CLAUDE.md:380` lists five fakes; thirteen ship.
- **Release mechanics.** `pyproject.toml` declares `0.4.0` and the repository has
  **no git tags at all**; `deploy.yml` triggers on `release: published`, so the
  deploy path has never fired. Add a tag↔version check and a release workflow.
- **CHANGELOG enforcement.** `CONTRIBUTING.md:88-89` requires an `[Unreleased]`
  entry naming a test; no job checks it. **Move this check into PR C**, or PRs A
  through G all ship under a rule that is not yet enforced.
- **Documentation entry point.** 52 markdown files across nine `docs/`
  subdirectories with no index. `NEXT_STEPS.md` is 652 lines and opens by
  delegating its roadmap elsewhere; split the ledger from the live roadmap.
- **Layout consistency.** `eval_harness_bridge/src/` holds two flat modules with
  no `__init__.py` and no `pyproject.toml`, reachable only through
  `pythonpath`/`mypy_path`, while its sibling `mango-integration-contracts/` is a
  proper package. `sitecustomize.py` at the repo root is imported by **every**
  Python process whose `sys.path` includes the root and mutates `PYTEST_ADDOPTS`
  globally, which `pyproject.toml`'s `addopts` and `tests/conftest.py` already do.

## Open decisions (carried, not scheduled)

Neither is a defect; both were mis-filed as security fixes in revision 1. Full
argument in spec-0031's "Decisions required".

- **D1 — per-invocation workflow opt-in over HTTP.** Specified in spec-0008
  (Implemented, acceptance criterion ticked), decided in ADR-0012, which
  explicitly **rejected** the remedy revision 1 proposed, and pinned by three
  tests. What is genuinely missing is a threat analysis: ADR-0012's trade-off
  section covers only 400-vs-404 modelling. Recommended resolution is additive —
  a `WorkflowSettings.allow_inline_definition` defaulting to `false` — leaving
  ADR-0012's ergonomics reachable and needing no supersession. The path-reading
  half is specified by the same sentence ("inline JSON or path") and carries the
  observable consequence of a filesystem existence oracle.
- **D2 — loop-budget ceiling.** A genuine gap (no spec or ADR discusses one), but
  revision 1's fix was wrong three ways: the proposed clamp inverts spec-0026's
  recorded precedence, `le=` on the DTO has no review record because the snapshot
  cannot see it (C1), and `le=` plus a clamp leaves the clamp unreachable under
  `core`'s 100% floor. Recommended resolution leaves `core/agent.py` untouched
  and adds `LoopSettings.max_steps_ceiling` enforced in `_effective_max_steps`.

## Deferred / out of scope

- **Streaming accumulation cap.** `orchestrator.py:690-710` accumulates the whole
  response with no bound. Revision 1's fix ("persist a truncation marker")
  inverts ADR-0025's invariant that "a persisted streamed turn is always the
  complete answer the client received" — `SummarizeAgent` would then feed a
  doctored turn back into a prompt. The honest options are to cap and *refuse to
  persist* past the bound, preserving persisted ⇔ complete, or to change the
  invariant, which needs ADR-0025 amended. Either way it is not a bullet in a
  small-fixes milestone.
- **Tenant identity derived from the authenticated principal.** `X-Tenant-ID` is
  sanitised but self-asserted, so with tenancy on and auth off any client reads
  another tenant's turns. ADR-0017 discusses row-filter versus schema-per-tenant
  isolation and never mentions trust or spoofing. A boundary change needing its
  own ADR; B4 mitigates by making auth the default posture.
- **CodeQL or another dedicated SAST**, and **container image scanning** (`trivy
  fs` scans the working tree, not the built image, so the pinned base layer and
  installed wheel closure are never scanned; the image is built only in
  `deploy.yml` with no scan before `docker push`). Both are real gaps and both
  need a recorded decision rather than a drive-by addition.
- **`cli/commands/eval.py` decomposition** — 182 of 318 lines are one typer
  signature that must stay positional, which is why `pyproject.toml:225` already
  grants a `PLR0917` exemption.
- **`tests/fakes.py` split** — deliberately one shared-doubles registry.
- **`CODEOWNERS`** — declined at `CONTRIBUTING.md:72-74`.
- **The ~34 `DEFAULT_*` config constants with no importer** — may be load-bearing
  under the no-hard-coded-values rule. Needs a human call before any removal.
- **Python 3.13 matrix leg, ruff `RET`/`PERF`, `pre-commit run --all-files` in
  CI** — deferred at `NEXT_STEPS.md:581`. C7 re-opens `C90` from that same line,
  explicitly and with measurement.
- **ruff `N`, `TRY`, `FBT`** — recorded as deliberately not selected, with
  reasoning. Unchanged.

## Verification

```bash
make gate                                          # full chain, CI's order
make coverage                                      # after every C6 ratchet
python -m pytest tests/deploy tests/tooling -q      # the guard suites C/D touch
```

After PR A and PR B:

```bash
python -m pytest tests/test_workflow_api.py tests/test_api.py -q
RUN_INTEGRATION=1 make integration
```

After PR C and PR F:

```bash
python -m pytest tests/test_import_compat.py tests/test_openapi_snapshot.py -q
make protected-paths BASE_REF=origin/feat/initial-release
```
