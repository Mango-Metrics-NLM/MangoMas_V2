# Code quality, tech-debt and enterprise readiness — delivery plan

- **Branch:** `claude/code-quality-tech-debt-plan-dag2y8`
- **Date:** 2026-09-16
- **Target release:** rolling → `v0.5.0`
- **Status:** Draft
- **Specs:** spec-0031
- **ADRs:** ADR-0030 required for PR A-M4 (`max_steps` ceiling) and PR E-M5
  (`orchestrator.py` seam extraction) — both protected paths. Every other
  milestone is additive or mechanical.

## Executive summary

The reflection began by running the real gate rather than reading about it.
**All twelve `make gate` steps pass on a clean tree at `df92e3d`** — 2683 tests
in 51s, 98.88% coverage against a 95% floor, `mypy --strict` clean over 433
files, both import-linter contracts kept. CI is green and this is not a
remediation program for broken code.

Two things came out of the audit that change the priority order.

**First, the verification engineering here is genuinely excellent and should be
said plainly.** Zero TODO/FIXME markers repo-wide. Zero unused imports, because
`ruff` enforces it. Every outbound HTTP client carries a configured timeout bar
one. Every SQL query is parameterised. Header sanitisation is a single shared
charset invariant applied before anything reaches a log or a SQL parameter. No
broad `except` swallows cancellation. That surface is verified clean and should
not be re-audited.

**Second, the HTTP surface has an unauthenticated cluster that the feature flags
do not actually gate.** This was verified by running the app in-process, not
inferred. With stock defaults — `MANGOMAS_WORKFLOW__ENABLED=false`,
`MANGOMAS_AUTH__ENABLED=false`, `MANGOMAS_API__MAX_BODY_BYTES=0` — a client can
execute arbitrary agent topologies, probe the filesystem, and crash the request
worker with a 16 KB body. That moves security from the end of this plan to the
front of it.

Everything else is **slack**: coverage floors sitting up to 10 points below what
the code already holds, ~1,000 lines of pure import-formatting noise, and six
places where a guard passes because its subject is missing rather than because
it is sound. Sequencing is driven by one constraint: **every mechanical change
must land behind a guard that would have caught it.** So gate integrity (PR B)
precedes the decompositions it polices (PR E), and the `orchestrator.py` split
goes last because converting it to a package would silently disable the
protected-path gate.

## Measured baseline (2026-09-16, commit `df92e3d`)

Every number below was produced by running the command.

| Gate step | Result |
|---|---|
| `validate-config`, `lint`, `format-check` | pass — 433 files formatted, 0 findings |
| `typecheck` (`mypy --strict`) | pass — 433 files, 0 issues |
| `lint-imports` | pass — 180 files, 477 deps, 2 contracts kept |
| `frontmatter` | pass — 17 skills, 27 agents |
| `protected-paths` | pass — no protected contract touched |
| `test` | 2683 passed, 66 skipped, 2 warnings, 51.5s |
| `coverage` | all 21 per-package floors met; global 98.88% |
| `bridge-coverage` / `contracts-coverage` | 100% / 100% |
| `scripts-coverage` | 96% against a 94 floor |

Structural metrics: 16,326 source lines against 35,740 test lines (2.19×);
496 functions, of which 25 exceed 50 lines and only 6 exceed 80; peak cyclomatic
complexity 15, and `ruff --select C901` passes at a threshold of 10.

Churn concentrates in the **governance meta-layer**, not the product. Across 267
commits the most-edited files are `tests/constants.py` (56), `pyproject.toml`
(32), `src/mangomas/config.py` (21), `tests/deploy/test_ci_make_parity.py` (19),
`Makefile` (19) and `.github/workflows/ci.yml` (16). The tooling that enforces
quality here costs more maintenance than the code it enforces — which is the
main argument for PR B making three hand-maintained registries self-checking.

## PR A — Close the unauthenticated HTTP cluster (spec-0031 W7)

Lands first. Every finding below was reproduced in-process against stock
defaults; the reproduction becomes the regression test.

### Milestone A1 — the workflow feature flag must gate the HTTP surface

- **Failing test first:** POST `/workflows/run` with an inline `definition` and
  `workflow.enabled=false`; assert 400. **Verified red today — returns 200 and
  executes the graph.**
- **Depends on:** nothing.
- `workflow/loader.py:35-45` only consults `cfg.enabled` when `definition is
  None`. This is *documented intent* — the docstring calls it "per-invocation
  opt-in" and it is correct for the CLI, where the caller is the operator. On
  the HTTP surface the caller is anonymous, so the same rule means anyone who
  can reach the port composes and runs arbitrary topologies over every
  registered agent, with their own `max_steps`.
- Fix: keep per-invocation opt-in for the CLI; require `cfg.enabled`
  unconditionally on the HTTP path, or add
  `workflow.allow_inline_definition` defaulting to `false`. Either way the
  CLAUDE.md claim that the feature is "off by default, existing deployments see
  no change" becomes true of the HTTP surface, which it is not today.

### Milestone A2 — no filesystem reads from a request body

- **Failing test first:** POST `/workflows/validate` with `definition:
  "/etc/passwd"` and with `"/nonexistent/zz"`; assert both return the same
  opaque error. **Verified red today** — the two responses are distinguishable:

| Input | Response |
|---|---|
| `/etc/passwd` | 400 `workflow definition is not valid JSON` |
| `/nonexistent/zz` | 400 `cannot read ... No such file or directory` |

- **Depends on:** A1.
- `loader.py:63-77` treats any `definition` not starting with `{` as a path and
  reads it with `Path(stripped).read_text()` — no allow-list, no root
  confinement, no size cap. That is a full-filesystem existence oracle, and any
  on-disk JSON matching the graph schema gets loaded and run. The read is also
  synchronous inside an async route, blocking the event loop for its duration —
  a CLAUDE.md async-I/O violation that `ruff`'s `ASYNC` family misses because the
  `open` sits in a sync helper.
- Fix: inline JSON only on the HTTP path. If paths must stay, confine via
  `Path.resolve().is_relative_to(root)`, cap the read, move it to
  `asyncio.to_thread`, and collapse all read failures to one message.

### Milestone A3 — bound graph depth and node count

- **Failing test first:** `load_workflow` on a deeply-nested graph; assert
  `ConfigError`, not `RecursionError`. **Verified red today, and worse than
  first reported:**

| Nesting depth | Body size | Result |
|---|---|---|
| 500 | 16 KB | `RecursionError` escapes the `ConfigError` boundary |
| 2000 | 64 KB | same |
| 5000 | 160 KB | same |

- **Depends on:** nothing.
- The comment at `graph.py:81-83` — "v1 keeps nesting bounded to depth two, so no
  recursion is possible" — is wrong: `WorkflowStep` includes `FanOutNode` and
  `BranchNode`, both of which recurse without limit. A depth-200 graph validates
  cleanly. `RecursionError` derives from `RuntimeError`, so it is not caught by
  `except json.JSONDecodeError` and never reaches the `ConfigError`
  normalisation; it surfaces as a 500 through the access-log middleware. With
  `max_body_bytes=0` by default this is an unauthenticated worker-stress
  primitive reachable with a 16 KB request.
- Fix: depth counter and node-count cap before `model_validate`, a byte cap on
  `source`, `max_length` on `branches`/`steps`, and catch `RecursionError` in
  `load_workflow`. `nodes/fan_out.py:54-56` also gathers every branch with an
  unbounded `asyncio.gather` — bound it.

### Milestone A4 — cap `max_steps` ⚠ protected path

- **Failing test first:** assert `AgentRequest(max_steps=10**9)` is rejected.
  **Verified red today — it is accepted.**
- **Depends on:** nothing. Requires a `BREAKING-CHANGE` trailer and ADR-0030.
- `core/agent.py:36` declares `ge=1` with no `le`, and
  `orchestrator.py:322-338` returns `request.max_steps` verbatim whenever it
  differs from the default, then drives `range(effective_max)` calls to
  `agent.handle`. Each iteration appends an assistant message, so the prompt
  grows alongside the call count. `loop.step_timeout_seconds` bounds each step;
  nothing bounds the total. One unauthenticated POST buys arbitrary upstream
  spend.
- Fix: add `le=DEFAULT_LOOP_MAX_STEPS_CEILING` to the field and clamp against
  `loop_settings.max_steps` as a server-side ceiling, not merely a fallback.

### Milestone A5 — harden the default and reference postures

- **Failing test first:** `test_deploy_manifest_sets_auth_and_backpressure` over
  `deploy/service.yaml`; assert `/docs` is absent when `env != "local"`.
- **Depends on:** nothing.
- `deploy/service.yaml:29-52` — the reference production manifest — sets
  `MANGOMAS_ENV=prod`, telemetry, provider and secrets vars, and **none** of
  `AUTH__ENABLED`, `AUTH__SECRET_REF`, `API__MAX_BODY_BYTES`,
  `API__MAX_CONCURRENT_REQUESTS`, nor an ingress annotation. With
  `containerConcurrency: 80` an operator following it verbatim publishes an
  unauthenticated LLM proxy. `deploy/README.md` mentions only CORS.
- Verified public on a stock app: `/docs`, `/redoc`, `/openapi.json` (never
  configured in `create_app`) and `GET /agents` (`system.py:38`, which leaks the
  agent roster and is the one execution-adjacent route without
  `Depends(require_auth)`).
- Fix: set auth and both backpressure knobs in the manifest, add
  `ingress: internal-and-cloud-load-balancing`, document the required set, gate
  the docs endpoints outside `local`, and add `require_auth` to `GET /agents`.

### Milestone A6 — stop leaking internals through error and readiness bodies

- **Failing test first:** assert the `/readyz` body carries no upstream URL and
  no raw driver text on failure.
- **Depends on:** nothing.
- `health.py:100,119` copies `str(exc)[:200]` into an unauthenticated body.
  `_http_errors.py` builds messages naming the LLM `base_url`;
  `_vertex_errors.py` names the GCP `project_id`. Worse on the DB side:
  `postgres.py:165,210` calls `await self._ensure_pool()` **outside** the `try`,
  so an asyncpg auth failure propagates unwrapped and
  `check_ready`'s broad `except Exception` copies e.g. `password authentication
  failed for user "…"` into the public response.
- Fix: move `_ensure_pool()` inside the `try` in both methods; emit only
  `type(exc).__name__` publicly and log the full text against the request id;
  suppress `detail` in the envelope when `env == "prod"`; truncate the
  untruncated tool-exception interpolation at `tool_agent.py:147`.

### Milestone A7 — small, isolated correctness fixes

- **Depends on:** nothing. Each is a few lines with its own regression test.
- `auth.py:105` — `secrets.compare_digest` raises `TypeError` on non-ASCII, so
  `Authorization: Bearer tökén` returns 500 instead of 401, and a non-ASCII
  configured token 500s every request. Compare encoded bytes. The comparison is
  otherwise correctly constant-time and fails closed.
- `adapters/llm/vertex.py:120,127` — `self._timeout_seconds` is assigned and
  never read; the three `generate_content_async` calls have no timeout. This is
  the **only** unbounded outbound call in the repo; every other client is
  correctly bounded.
- `rag/retrieval.py:154-156` — `top_k` from the model's tool-call JSON gets
  `max(1, …)` but no ceiling, so retrieved content can drive an enormous query
  (prompt injection). Clamp it.
- `workflow/predicate.py:81,86` — client-supplied regex compiled and run per
  loop iteration with no complexity bound; combined with A1 and A4 the client
  controls both the pattern and the iteration count. Cap the pattern length.
- `orchestrator.py:690-710` — streaming accumulates the whole response in a
  `list[str]` for turn persistence with no cap. Cap it and persist a truncation
  marker past the bound.
- `adapters/storage/memory.py:38-42` — `f"{prefix}-{today}.md"` is unsanitised,
  so a `../` prefix escapes the memory dir. Latent today (no caller passes one)
  but the protocol exposes it.

### Milestone A8 — secret containment

- **Failing test first:** `assert "secret" not in repr(Settings(...))`.
- **Depends on:** nothing.
- `SecretStr` appears **nowhere** in `src/`. `LLMSettings.api_key` is plain
  `str` and receives the resolved secret — which for Vertex is the
  **service-account JSON body** (`composition/llm.py:61`). `DBSettings.url`
  carries a DSN with a password. No current call site logs a settings object, so
  this is a latent footgun rather than an active leak, but nothing prevents one.
- Fix: `api_key: SecretStr`, `db.url: SecretStr`, `.get_secret_value()` at the
  two adapter construction points.
- **Deliberately not changed:** tenancy identity. `X-Tenant-ID` is sanitised but
  self-asserted, so with tenancy on and auth off any client reads another
  tenant's turns. Deriving tenant from the authenticated principal is the right
  fix but it is an ADR-0017 boundary change, not a hardening patch — see
  Deferred.

## PR B — Gate integrity: close the fail-open holes (spec-0031 W4)

These guards must exist before the refactors they police.

### Milestone B1 — `.env.example` value contract

- **Failing test first:** `test_env_example_values_match_field_defaults`,
  comparing each `KEY=value` against `model_fields[...].default` with an
  allowlist for illustrative blocks. **Verified red on four lines:**

| Line | States | Real default |
|---|---|---|
| `LOG__BODY_TRUNCATE` | 2000 | 512 |
| `API__READY_TIMEOUT_SECONDS` | 5.0 | 2.0 |
| `API__HISTORY_DEFAULT_LIMIT` | 50 | 10 |
| `API__HISTORY_MAX_LIMIT` | 500 | 1000 |

- **Depends on:** nothing.
- All four are commented, and the file header says commented blocks are opt-in
  features — but sibling commented lines in the same blocks (`LOG__FORMAT=text`)
  *do* restate the real default, so nothing tells a reader which kind of line
  they are looking at. The vertex/gcp/postgres/eval-threshold blocks genuinely
  show non-defaults and belong on the allowlist; the allowlist is the review
  record.

### Milestone B2 — every documented setting must have a consumer

- **Failing test first:** `test_every_documented_setting_has_a_consumer`.
  **Verified red on three:** `api.ready_timeout_seconds`, `api.host`,
  `api.port` are read by nothing outside `config/`.
- **Depends on:** nothing.
- The existing contract test checks names in both directions and defaults in
  one, but never that a field reaches code — which is the blind spot that let a
  30× readiness-budget gap ship. `MANGOMAS_LOG__BODY_TRUNCATE` is a fourth
  instance: documented as "max chars of request/response body in access logs",
  consumed by nothing. (The access-log middleware logs no bodies and no headers
  at all, which is correct — the setting is simply orphaned.)

### Milestone B3 — facade registry completeness

- **Failing test first:** `test_facade_registry_covers_every_submodule`,
  enumerating each registered package on disk. Mutation-prove with a scratch
  module.
- **Depends on:** nothing.
- Verified complete today (cli 8/8, telemetry 6/6, config 13/13,
  composition 12/12, api.middleware 3/3), so this is a ratchet over a property
  already held — the posture `pyproject.toml`'s lint-family comment already
  argues for.

### Milestone B4 — pre-commit ↔ pyproject revision lockstep

- **Failing test first:** `test_precommit_revs_match_pyproject_pins`, asserting
  `ruff==0.16.0` ↔ `rev: v0.16.0` and `mypy==2.3.0` ↔ `rev: v2.3.0`.
- **Depends on:** nothing.
- Both files carry a "keep in lockstep" comment; the four existing parity tests
  never check it. Drift means the hook runs a different linter than CI — the
  "green locally, red in CI" class. The identical duplication for the coverage
  floor *is* guarded at `test_ci_make_parity.py:226`; this pair never adopted
  the pattern. `.pre-commit-config.yaml:29-36` also restates eight dependency
  ranges from `pyproject.toml`, including a `starlette>=0.40` that is not a
  declared dependency at all.

### Milestone B5 — protected-path governance must survive a package conversion

- **Failing test first:** build a scratch tree where a protected module has
  become a package, run the gate's `check()`, assert non-zero exit. **Verified
  red:** `check_protected_paths.py:128` computes `set(changed) &
  protected_paths`, an exact string intersection, so
  `core/orchestrator/dispatch.py` matches nothing and the gate prints "No
  protected core contracts changed. OK."
- **Depends on:** nothing. **Blocks PR E-M5.**
- Fix: match an entry as an exact path *or* a directory prefix, and prove the
  new matcher fails closed. Mirror it in `lint_agent_frontmatter.py`'s
  `_check_protected_path` and `_protected_paths_in_command`, which use the same
  literals.

### Milestone B6 — coverage floor ratchets

- **Depends on:** nothing. The gate is its own proof; run `make coverage`.
- Raise each floor to `measured − 2`, the two-point margin the `SCRIPTS_FLOOR`
  comment already establishes as this repo's convention. Never to the exact
  measured value — a one-point fluctuation would then break CI for no defect.

| Floor | Measured | Current | Proposed |
|---|---|---|---|
| adapters | 95 | 85 | 93 |
| cli | 100 | 95 | 98 |
| config | 100 | 95 | 98 |
| workflow | 100 | 95 | 98 |
| harness | 100 | 95 | 98 |
| api | 99 | 95 | 97 |
| eval | 99 | 95 | 97 |
| composition | 99 | 95 | 97 |
| agents | 99 | 95 | 97 |
| telemetry | 99 | 95 | 97 |
| global | 99 | 95 | 97 |

`metrics`, `rag` and `cognitive` measure 97 and stay at 95 — already at the
margin. The eight 100/100 floors are unchanged. The global floor also lives in
`pyproject.toml`'s `--cov-fail-under`; both move in one commit, which
`test_ci_make_parity.py:226` already enforces.

### Milestone B7 — complexity and warning ceilings

- **Depends on:** nothing. Both are ratchets over properties already held.
- Add `C901` with `max-complexity = 15` (measured peak is 15; passes at 10
  today). Record the reasoning beside the existing not-selected list, which
  currently records `C90` as deliberately absent — this reverses that with
  measurement.
- Add `filterwarnings = ["error", ...]` to `[tool.pytest.ini_options]`, with the
  two known upstream deprecations ignored by name. Today nothing fails on a new
  deprecation, so upstream decay stays invisible until it becomes a break.

### Milestone B8 — close two coverage-shaped gaps the audit surfaced

- `MAX_TENANT_ID_LENGTH` (`tenancy.py:40`) has **no truncation test**, despite
  `tenancy.py` sitting in a 100%-floor group. Its twin
  `MAX_CORRELATION_ID_LENGTH` is asserted in two files. A security-relevant
  clamp with a 100% number and no pin is exactly what `mango-coverage-audit`
  exists to catch.
- Five `SIGNAL_*_ENV` constants are defined, exported and used by nothing —
  `GENAI_SPANS`, `HTTP_URL`, `HTTP_TIMEOUT_SECONDS`, `POLICY_VERSION`,
  `SCHEMA_VERSION`. Their three siblings are used. That reads as missing
  env-override coverage on the signal settings rather than surplus constants.

## PR C — CI/CD economics and release integrity (spec-0031 W1)

### Milestone C1 — concurrency groups

- **Failing test first:** `test_every_workflow_declares_a_concurrency_group`.
  Red on all four.
- `ci.yml` triggers on `push: ["**"]` *and* `pull_request`, so **every push to
  an open PR branch runs all eight jobs twice**, and stacked pushes never cancel
  superseded runs. Add `cancel-in-progress: true`, except `deploy.yml` where
  cancelling mid-rollout is worse than queueing. Largest cost win available.

### Milestone C2 — job timeouts

- **Failing test first:** `test_every_job_declares_a_timeout`. Red everywhere —
  no job sets `timeout-minutes`, so the default is six hours. `eval-gate.yml`'s
  background uvicorn and `deploy.yml`'s smoke loop are the realistic hangs.

### Milestone C3 — the deploy gate must be the gate

- **Failing test first:** `test_deploy_verify_runs_the_full_gate`. Red today.
- `deploy.yml`'s `verify` runs `make test`, `make coverage` and
  `make contracts-coverage` — three of twelve steps. It omits `lint`,
  `format-check`, `typecheck`, `lint-imports`, `frontmatter`,
  `protected-paths`, `bridge-coverage` and `scripts-coverage`, so **a release
  can deploy code that fails type-checking**. Replace the three with `make gate`.

### Milestone C4 — close the `sbom-scan` fail-open

- **Failing test first:** `test_no_needed_job_is_continue_on_error`. Red today.
- `nightly.yml:127` marks `sbom-scan` `continue-on-error: true` while listing it
  in `notify`'s `needs:`, so the job can never reach a failed conclusion and
  `if: failure()` never fires for it. The existing guard checks `needs:`
  membership, not whether the job can fail — which is why it passes. Keep the
  scanner non-blocking via `trivy --exit-code 0`; drop `continue-on-error` so a
  *crashed* scan still reports.

### Milestone C5 — cache the analysers, collapse redundant installs

- **Depends on:** C1.
- `make install` runs seven times per `ci.yml` run, and nothing caches
  `.mypy_cache`, `.ruff_cache` or `.import_linter_cache`, so `mypy --strict`
  starts cold every time (warm locally: 1s). Cache all three on `lint`, and
  merge the three isolated coverage jobs into one three-step job — that keeps
  per-step failure attribution, which is the stated reason they are separate,
  while removing two full installs.

### Milestone C6 — required-checks and PR-template drift

- **Failing test first:** `test_contributing_required_checks_match_ci_job_names`
  — passes today (9 documented names, 8 jobs, exact match), so it lands as a
  ratchet. Branch protection matches on check *name*, so a rename silently
  desyncs the doc and breaks the live rule.
- The checks are documented but **not yet actually required** on
  `feat/initial-release` — recorded in four places as decision D1, "the cheapest
  unblock in the register". Until an admin flips it, a trailer-less
  protected-path PR shows red and merges anyway.
- `.github/PULL_REQUEST_TEMPLATE.md:17-23` lists raw commands over
  `src tests scripts` rather than `make` targets over the wider `CODE_PATHS`.

## PR D — Dependency determinism (spec-0031 W2)

### Milestone D1 — constrain the CI install

- `requirements.lock` pins the transitive runtime closure but is consumed
  **only by `Dockerfile:47`**. CI runs `pip install -e ".[dev]"` unconstrained,
  so a green pipeline can turn red overnight from an upstream release with no
  local change — `pytest` resolved to 9.1.1 here against a `>=8.2` floor. Either
  extend the lockfile to the dev extra and pass it as constraints from
  `make install`, or pin the remaining floors. The four analysers are already
  exact-pinned for precisely this reason; the argument generalises.

### Milestone D2 — declare or guard `mango_contracts`

- `mango_contracts` is imported **unconditionally at module top level** in seven
  places under `src/mangomas/cognitive/` and appears in no dependency list and
  no extra. It is reachable only via a manual `pip install -e
  ./mango-integration-contracts`, pytest's `pythonpath`, or the Dockerfile's
  second wheel. This is deliberate and documented (keeping `requirements.lock`
  an `==`-pin of PyPI names), and the *flag-off* path is genuinely safe.
- But the flag-**on** path in a plain `pip install mangomas` environment raises a
  bare `ImportError`, not a typed `ConfigError` with an install hint.
  `eval/_langfuse.py:32-37` already does exactly the right thing for its
  optional SDK. Add a `mangomas[cognitive]` extra or adopt the same lazy-import
  guard.

### Milestone D3 — Dependabot and extras coverage

- Add the `docker` ecosystem so the digest-pinned base image stops rotting by
  hand, and `groups:` so an action bump is one PR rather than N.
- `pip-audit` audits the installed env, which is `.[dev]` — so the
  `google-cloud-aiplatform`, `chromadb`, `sentence-transformers` and `langfuse`
  trees are **never scanned**. Add a second audit over the cloud/rag extras.
- Nothing verifies `requirements.lock` still matches `pyproject.toml`. Add a
  drift check.

## PR E — Oversized-module reduction (spec-0031 W3)

Ascending risk. Each extraction leaves a permanent re-export facade per
ADR-0019 and registers it in `_FACADES` in the same commit, which B3 enforces.

### Milestone E1 — `combine-as-imports` (highest value/risk ratio in the plan)

- **Depends on:** B3. Pure formatting; the proof is `test_import_compat.py`
  staying green unchanged.
- **Measured on `config/__init__.py`: 533 → 305 lines, with all 128 import
  bindings and all 127 `__all__` entries byte-identical.** That file has zero
  functions and zero classes — it is not a god file, it is 381 lines of isort's
  one-alias-per-block default. Repo-wide there are 546 aliased import statements
  across 9 facade files, the largest being `tests/constants/__init__.py` (277
  aliases, 626 lines). One setting, one mechanical reformat commit, roughly a
  thousand lines of noise removed.

### Milestone E2 — `adapters/llm/vertex.py` (429 → ~230)

- Extract `_vertex_sdk.py` (lazy bootstrap + credentials, all `pragma: no
  cover`) and `_vertex_wire.py` (pure role/content mappers) along the three
  banner-marked clusters that already exist. The payoff is not line count: the
  `adapters` floor sits at 85 *because* SDK-bound code is unreachable in CI.
  Isolating it lets the pure mappers be measured at 100% and supports B6's
  ratchet to 93.

### Milestone E3 — `cognitive/producer.py` (285 → ~130)

- Extract `_payloads.py` (pure truncation/shaping) and `_lineage.py` (trace id,
  event ids, digests). Constraints: the module keeps importing nothing from
  `mangomas.agents`, and `_field_max_length` runs at import time reading
  Pydantic metadata — that binding stays with whichever module owns truncation.

### Milestone E4 — `scripts/lint_agent_frontmatter.py` (847, largest file in repo)

- Four independent, already-banner-separated modes. `scripts/_governance.py` and
  `scripts/_stdin_json.py` are the precedent for sibling extraction. **Hard
  constraint:** hook modes must stay importable with neither `pydantic` nor
  `pyyaml` present, so any module on the hook path is stdlib-only. Update
  `Makefile:24`'s `SCRIPTS_TESTS` in the same commit — it hard-codes test paths,
  and a missed entry drops tests out of the `scripts-coverage` gate silently.

### Milestone E5 — `core/orchestrator.py` (721 → ~390) ⚠ highest risk

- **Depends on: B5. Do not start before B5 lands.**
- **Do not convert it to a package.** `core/` has a module-facade precedent
  (`tools.py` → `structured.py`), not a package one, and a package conversion
  silently disables the protected-path gate.
- Extract a sibling `core/_topology.py` holding `FanOutOutcome` plus the
  functions that collapse the file's real debt: `_dispatch_once:264-312` and
  `dispatch_pipeline:429-478` are **49 and 50 lines of the same copy-pasted
  acceptance loop**, and `_effective_max_steps` / `_pipeline_effective_max_steps`
  are two near-identical precedence chains.
- Add `core/_topology.py` to `[tool.mangomas.governance].protected_paths` **in
  the same commit that creates it**, with a pin test mirroring
  `test_core_structured_is_a_protected_path`. Requires a `BREAKING-CHANGE`
  trailer and ADR-0030.

### Milestone E6 — `tests/constants/corpus.py` (712, pure data)

- 89 constants under six existing banner sections, zero logic. Split behind the
  `tests.constants` facade. Justified by churn: its lineage is the most-edited
  file in repository history at 56 commits.

## PR F — Dead and redundant code (spec-0031 W6)

The mechanical axes are already clean and should be recorded as such: **zero
TODO/FIXME/HACK/XXX markers** anywhere, **zero commented-out code** (`ERA` is
selected and was measured at zero before being locked in), and **zero unused
imports** under the project's own `ruff` config. Every declared dependency and
every extra resolves to a real import. Every `tests/fakes.py` member is used.
The findings below are all *surface that outlived its consumer*.

### Milestone F1 — retire the third copy of protected-path governance

- `src/mangomas/harness/governance.py` is 141 lines with **no production
  consumer** — its own docstring says so. It is a third implementation of logic
  that exists in `scripts/_governance.py` and
  `scripts/lint_agent_frontmatter.py`, including a **byte-identical marker
  regex**, and the protected-path list is stated in four places. Roughly 90
  duplicated lines.
- The recorded justification (`scripts/` must run without `mangomas` installed)
  is sound for the two `scripts/` copies. It does **not** justify the third,
  which exists only so the logic lands inside `--cov=mangomas` — so the coverage
  number measures code that never executes, and it is the copy most likely to
  drift from the one CI actually runs. Retire it or make it the single library
  the scripts vendor from.

### Milestone F2 — correct four documents about a dead code path

- `--check-protected-paths` (~45 lines) has **no invoker anywhere**: not
  `.pre-commit-config.yaml` (which passes no flag), not the `Makefile`, not any
  workflow, not any hook. `CLAUDE.md:542-544`, the script's own docstring,
  `ADR-0021:57` and `spec-0017:97` all still describe it as a live pre-commit
  hook. Only `mango-harness/SKILL.md:112` has been corrected.
- The code is harmless; the documentation is not, because it describes a defence
  layer that does not exist. Either wire it into pre-commit or delete it and fix
  the four documents.

### Milestone F3 — decide on the structured-output helpers

- `parse_or_recover` and `parse_llm_json_object` (both on a **protected path**)
  have **zero production callers**. Worse, the design says otherwise:
  `structured.py:117-118` names the LLM-judge scorer as a consumer, and
  `spec-0015:203-205` states the planner/reviewer path adopted the helper. In
  fact `agents/_structured.py:96-121` calls `model_validate_json` directly, so
  the brace-span recovery these helpers provide — the documented failure mode of
  local models emitting fenced JSON — **is not reachable from planner or
  reviewer**, and `eval/scorers/llm_judge.py:80-92` hand-rolls 13 lines of the
  same logic.
- This needs a human decision, not a cleanup: either give the helper a
  `message=` parameter and adopt it in both places, or delete it and drop the
  false claims from the docstring and the spec. The second audit flagged the
  same uncertainty independently — it reads as an unnoticed regression during
  the spec-0015 decomposition rather than a deliberate narrowing.

### Milestone F4 — collapse the remaining duplication clusters

- `_FLAG_BY_NAME` + `_resolve_flags` are byte-identical in
  `eval/scorers/regex_match.py:26-40` and `workflow/predicate.py:30-60` (~20
  lines). The layering constraint is real, but this repo already solved that
  exact shape twice by promoting shared mechanics to a neutral leaf
  (`_headers.py`, `_entry_points.py`). A `_regex_flags.py` satisfies both.
- The discovery idempotency latch is duplicated in `agents/discovery.py` and
  `eval/discovery.py` (~18 lines each). `_entry_points.py`'s docstring carefully
  explains what stays caller-side and why — that reasoning covers the loop body,
  not the latch, which has no divergence.
- Test-side: **ten separate `EvalReport` builders** (three byte-identical), the
  workflow `_NamedChat`/`_orch`/`_req`/`_graph` quartet duplicated four times,
  and eleven independent `_req` definitions. `tests/composition/helpers.py`
  already establishes the fix pattern; `tests/eval/` simply never adopted it.

### Milestone F5 — small removals

- Two unused fixtures: `fake_memory` and `fake_tool` (`tests/conftest.py:212,226`).
- Nine unused `tests/constants` members — five of which are the `SIGNAL_*_ENV`
  cluster from B8, which is a test gap rather than surplus.
- `# approved-breaking-change` alias: **0 occurrences in all 267 commits**,
  against 16 for `BREAKING-CHANGE`. Compat for a form never used.
- `EvalRunner.run(agent_name=...)` legacy positional: no production caller, kept
  alive by ~18 test call sites. `CLAUDE.md:380` lists five fakes; thirteen ship.

## PR G — Hard-coded and inert configuration (spec-0031 W5)

The `DEFAULT_*` discipline in `src/` is genuinely strong — the audit found no
unexplained magic number in application code, and the two most suspicious
duplicates turned out to be deliberate and mechanically pinned. The defects are
at the edges.

### Milestone G1 — wire or retire the inert settings

- **Depends on:** B2.
- Wrap both readiness probes in `asyncio.timeout(api.ready_timeout_seconds)` —
  today `/readyz` is unbounded and the effective budget is the 60s LLM timeout,
  30× the documented 2.0s. For `host`/`port`, wire them to the serving
  bootstrap or retire them the way `MANGOMAS_RAG__MIN_CHUNK_WORDS` was retired
  at `f8d37a1`. Leaving an **uncommented** `MANGOMAS_API__PORT=8000` in
  `.env.example` beside the real `PORT` contract is the trap — `8000` appears
  12 times across 6 files with nothing binding them together.

### Milestone G2 — re-export the restated test constants

- `tests/rag/test_chunker.py:18` (800/120),
  `tests/composition/helpers.py:80-81` (provider names),
  `tests/adapters/test_shared_errors.py:17` (base URL), and
  `tests/integration/test_tenancy_stream_flow.py:35` (needs
  `DEFAULT_TENANCY_HEADER` re-exported first). Add a guard so the
  `X as X` rule stops being prose.

### Milestone G3 — name the duplicated literals

- `_SDK_INSTALL_HINT` is byte-identical in `adapters/llm/vertex.py:60-62` and
  `adapters/embeddings/vertex.py:27-29`; both already import from
  `adapters/_vertex_errors.py`, the obvious home.
- The `"source"` vector-metadata key is restated across `rag/pipeline.py`,
  `rag/retrieval.py` and `adapters/vector/chroma.py` — a genuine cross-layer
  wire contract that `rag/` cannot import from `adapters/`.
  `cognitive/constants.py` is the in-repo precedent for naming exactly this.
- `cognitive/producer.py:150,185` slices `[:16]` twice for a digest id while
  every neighbouring bound is a named constant.
- **Recorded decisions, not fixes:** `_REQUEST_ID_HEADER` is fixed while
  `MANGOMAS_TENANCY__HEADER` is configurable; `DEFAULT_ERROR_DETAIL_TRUNCATE`
  has no env var while `LOG__BODY_TRUNCATE` does. Both asymmetries want a
  written rationale either way.
- Narrow `pyproject.toml:215`'s blanket `S603`/`S607` ignore for `scripts/*` to
  per-line `noqa`. The stated justification is about `T201`/`PTH` and does not
  cover the subprocess family, so a future untrusted-input `subprocess.run`
  would land silently. Also reword `harness/governance.py:123-124`, whose
  comment claims the path is "not attacker-controlled" when it originates from
  hook stdin — the real defence is the fixed argv plus `--`.

## PR H — Enterprise organisation (spec-0031 W8)

### Milestone H1 — release mechanics

- **Depends on:** C3.
- `pyproject.toml` declares `version = "0.4.0"` and **the repository has no git
  tags at all**. `deploy.yml` triggers on `release: published`, so the deploy
  path has never fired. Add a tag↔version consistency check and a release
  workflow. `mangomas.__version__` already derives correctly from installed
  metadata; nothing ties that to a tag.

### Milestone H2 — CHANGELOG enforcement

- `CONTRIBUTING.md:88-89` requires an `[Unreleased]` entry naming a test and the
  PR template has a section for it, but no job checks it.

### Milestone H3 — documentation entry point

- 52 markdown files across nine `docs/` subdirectories with no index. Add
  `docs/README.md` mapping the four artifact types the specs README already
  defines to their directories.
- `NEXT_STEPS.md` is 652 lines and opens by delegating the forward roadmap to an
  analysis document. Split the historical ledger from the live roadmap.

### Milestone H4 — repository-layout consistency

- `eval_harness_bridge/src/` holds two flat modules with no `__init__.py` and no
  `pyproject.toml`, reachable only through `pythonpath`/`mypy_path`, while its
  sibling `mango-integration-contracts/` is a proper installable package. Make
  it a package or document why it deliberately is not.
- `sitecustomize.py` at the repository root is imported by **every** Python
  process whose `sys.path` includes the root, and mutates `PYTEST_ADDOPTS`
  globally. `pyproject.toml`'s `addopts` and `tests/conftest.py` already do this.
  Scope the workaround to the test session, or record why all three layers are
  needed.

## Deferred / out of scope

- **Tenant identity derived from the authenticated principal.** `X-Tenant-ID` is
  sanitised but self-asserted, so with tenancy on and auth off any client reads
  another tenant's turns. ADR-0017 discusses row-filter versus schema-per-tenant
  isolation and never mentions trust or spoofing. This is a boundary change
  needing its own ADR, not a hardening patch. PR A-A5 mitigates by making auth
  the default posture; the note must be added to ADR-0017 regardless.
- **CodeQL or another dedicated SAST.** Ruff's `S` family is the only static
  security signal and performs no taint analysis. A real gap, but a decision
  with its own cost — it needs a recorded rationale, not a drive-by addition.
- **Container image scanning.** `make sbom-scan` runs `trivy fs`, which scans the
  working tree, not the built image, so the pinned base layer and the installed
  wheel closure are never scanned. The image is built only in `deploy.yml`, with
  no scan before `docker push`. Same decision as the SAST question.
- **`cli/commands/eval.py` decomposition** — 182 of its 318 lines are one typer
  signature that must stay positional, which is why `pyproject.toml:225` already
  grants it a `PLR0917` exemption. Lowest value/effort ratio measured.
- **`tests/fakes.py` split** — deliberately one shared-doubles registry, called
  out as such in `CLAUDE.md`.
- **`CODEOWNERS`** — explicitly declined at `CONTRIBUTING.md:72-74`. Re-opening
  must revisit that decision.
- **The ~34 `DEFAULT_*` config constants with no importer** — may be load-bearing
  under the no-hard-coded-values rule, or accumulated surface. Needs a human
  call before any removal.
- **Python 3.13 matrix leg, ruff `RET`/`PERF`, and a `pre-commit run --all-files`
  CI job** — already deferred at `NEXT_STEPS.md:582-583`. B7 adopts `C901`
  specifically, with measurement; the rest stay deferred.
- **ruff `N`, `TRY`, `FBT`** — recorded as deliberately not selected, with
  reasoning (N818 would rename the public error taxonomy on a protected path).
  Unchanged.

## Verification

```bash
make gate                      # the full pre-PR chain, in CI's order
make coverage                  # per-package floors after every B6 ratchet
python -m pytest tests/deploy tests/tooling -q   # the guard suites PRs B/C touch
```

After PR A, additionally:

```bash
python -m pytest tests/test_workflow_http.py tests/test_api.py -q
RUN_INTEGRATION=1 make integration
```

After PR E, additionally:

```bash
python -m pytest tests/test_import_compat.py -q  # facade identity + completeness
make protected-paths BASE_REF=origin/feat/initial-release
```
