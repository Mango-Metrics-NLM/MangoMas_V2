# Toolchain and acceptance parity — delivery plan

- **Branch:** `claude/council-review-replan-vaz9rp` (plan + analysis only); one branch per PR block below
- **Date:** 2026-09-19
- **Target release:** rolling; PR C is a precondition for the v0.4.0 cut (D12)
- **Status:** Draft
- **Specs:** none written yet, and deliberately **unnumbered**. Each PR block
  names the spec it needs by slug; the spec lands before that block's code, per
  `CLAUDE.md` § Spec-Driven Development, and takes the next free integer **at
  the moment it is created**. `specs/0033` is the highest in use. A reserved
  number that drifts is worse than no number at all — the governance-hardening
  plan lost `spec-0032`/`spec-0033` to a concurrent branch exactly that way.
- **ADRs:** none — no boundary changes. D13 (`requirements.lock` ownership) is a
  register decision, not an ADR.
- **Source:** [`docs/analysis/20260919-council-rejection-and-replan.md`](../analysis/20260919-council-rejection-and-replan.md)
- **Relationship to the plan of record:**
  [`20260916T214636Z-reliability-evidence-plan.md`](20260916T214636Z-reliability-evidence-plan.md)
  is unchanged and remains the spine. This plan holds only what that plan does
  not cover: the two unguarded surfaces (N1, N2), the loader guard that PR B
  shipped without, and the release reconciliation. PRs A and B here are
  parallel-safe with everything there.

## Executive summary

Four small PRs, none longer than a few days, that close the two findings no
in-tree document covers and finish one that shipped half-done. The sequencing
constraint is blast radius: **PR A touches what production installs**, so it
goes first even though it is the least interesting; PR B closes a local-versus-CI
divergence that four open Dependabot PRs are actively widening, so it goes
before that queue is drained; PR C finishes spec-0032's milestone B1 and adds
the guard that makes `json_field` the *supported* path rather than merely an
available one; PR D reconciles the release numbering so `deploy.yml` can execute
for the first time. Then the plan of record's PR A (`make guard-probe`) resumes
as the main line.

Every milestone below is in the repo's own signature defect class — a constraint
asserted in prose or documentation while nothing mechanises it. That is why each
one is a test, not a doc change.

---

## PR A — Gate what production installs (spec slug: `lockfile-integrity`)

`requirements.lock` is the constraints file the runtime image installs through
(`Dockerfile:45-47`). It is not audited, has never been updated since creation,
and nothing checks it against `pyproject.toml`. A green test
(`tests/deploy/test_workflow_hardening.py:185`) documents the opposite. See
analysis §N2.

**Blocked on D13** for the rename half only. Milestones A0–A2 are correct under
either disposition and need no decision.

### Milestone A0 — the lock must match pyproject

- **Failing test first:** `tests/deploy/test_lockfile_freshness.py::test_every_runtime_distribution_is_pinned_in_the_lockfile`
  — parse `[project].dependencies` names, assert each appears as an `==` pin in
  `requirements.lock`. Prove it two-sided per `mango-mutation-proof`: add a
  synthetic runtime dependency to a `tmp_path` pyproject copy and assert the
  check fails; remove it and assert it passes. A one-sided version of this test
  passes against an empty lockfile with zero runtime deps parsed, which is the
  silent-pass shape this repo has already hit four times.
- **Depends on:** nothing — parallel-safe.
- Compare distribution **names** normalised per PEP 503 (`_-.` → `-`,
  case-folded); `opentelemetry-api` vs `opentelemetry_api` is the first thing
  that will bite. Do not compare specifiers — the lock pins the closure, the
  ranges do not, and asserting version agreement would fail correctly-generated
  locks.
- Extras are out of scope: the lock is generated with `--strip-extras`, so
  `uvicorn[standard]` resolves to `uvicorn` plus its closure. Assert on the base
  name only, and say so in the test docstring.

### Milestone A1 — audit what ships, not what resolves

- **Failing test first:** `tests/deploy/test_ci_make_parity.py::test_pip_audit_covers_the_runtime_lockfile`
  — the `pip-audit` target must name `requirements.lock`. Red today.
- **Depends on:** A0 (auditing a stale lock reports on versions nobody ships).
- Extend `make pip-audit` to audit the lock in addition to the installed
  environment: `pip-audit -r requirements.lock`. Keep both — the installed-env
  audit covers dev tooling, the lock audit covers production. Two invocations in
  one target, per the one-place rule `test_ci_make_parity.py` enforces.
- Expect this to go red on first run. A CVE in the pinned closure is the finding,
  not a failure of the milestone. Fix by regenerating the lock, not by loosening
  the audit.

### Milestone A2 — correct the claim the test makes

- **Failing test first:** none — this is a docstring and comment correction, and
  writing a test that asserts a docstring's wording would be the same defect in
  a new place.
- **Depends on:** A0, A1 (the claim becomes true once they land).
- Rewrite `test_dependabot_covers_actions_and_python_ecosystems`'s docstring
  (`tests/deploy/test_workflow_hardening.py:181-189`) to say what it verifies —
  that three ecosystems are configured — and drop the `requirements.lock`
  freshness claim, which A0 now actually enforces. Same for
  `.github/dependabot.yml:11-14`.
- ⚠️ If D13 chooses the rename (`requirements.lock` → `requirements.txt`), this
  milestone also lands the rename: `Dockerfile`, the three `tests/deploy/`
  references, and the generation comment in the file header. Hold the rename
  until D13 is taken; A0 and A1 do not wait for it.

---

## PR B — Close the toolchain parity hole (spec slug: `hook-dependency-parity`)

`tests/tooling/test_toolchain_pin_parity.py` exists precisely to stop the
pre-commit hooks drifting from `pyproject.toml`. Its `TOOL_PINS` tuple covers
`ruff` and `mypy` revs. The mypy hook's eight `additional_dependencies` entries
are unguarded, and four open Dependabot PRs each raise one of their floors above
what the application runs. See analysis §N1.

### Milestone B0 — the hook's dependency floors must not exceed runtime's

- **Failing test first:** `tests/tooling/test_toolchain_pin_parity.py::test_mypy_hook_dependencies_agree_with_runtime_ranges`
  — for every `additional_dependencies` entry naming a distribution that appears
  in `[project].dependencies`, the hook's specifier must equal the runtime
  specifier. Red only after a bump lands, so prove it against a fixture: a
  `tmp_path` pair where the hook says `starlette>=1.6.0` and pyproject says
  `>=0.40` must fail.
- **Depends on:** nothing — parallel-safe, and **land before draining the
  Dependabot queue**, or the four `pre_commit` PRs merge clean and the test
  arrives red.
- `starlette` is the interesting case: it is in the hook but **not** in
  `[project].dependencies` (it arrives transitively via `fastapi`). Decide
  explicitly and record it in the test docstring — either assert transitive-only
  entries against the lockfile pin (which A0 makes reliable), or exempt them by
  name with the reason. Do not let it fall through the `in pyproject` check
  silently; that is how the entry drifted in the first place.
- Grow the contract the way `TOOL_PINS` does — a module-level tuple, no
  distribution names in the test body.

### Milestone B1 — drain the queue

- **Failing test first:** none — this is queue work, gated by the existing
  suite. `make gate` plus B0 is the acceptance.
- **Depends on:** B0.
- **`ruff` needs one commit, not two.** `dependabot/pip/ruff-0.16.7` and
  `dependabot/pre_commit/https-/github.com/astral-sh/ruff-pre-commit-0.16.7`
  each fail `test_toolchain_pin_parity.py` alone. Land one branch bumping
  `pyproject.toml:45` and `.pre-commit-config.yaml:6` together, then close both
  Dependabot PRs as superseded. Same shape for `mypy`
  (`pyproject.toml:48` + `.pre-commit-config.yaml:23`) if its pre-commit rev PR
  appears.
- **The four `pre_commit` runtime-name PRs are typecheck-scope changes, not
  runtime bumps** — each edits one `additional_dependencies` line. Under B0 they
  are only mergeable alongside the matching `pyproject.toml` range change. Treat
  each as "do we want to support this range?", decide per distribution, and
  close the ones we do not.
- **CI-action majors batch.** The five `github_actions` branches land as one
  batch behind the existing SHA-pin policy and
  `tests/deploy/test_workflow_hardening.py`'s first-party/third-party split.
- ⚠️ `pyproject.toml` is a protected path (ADR-0030). Every commit touching it
  here needs a trailer, and prefer the path-scoped form:
  `BREAKING-CHANGE: pyproject.toml — <rationale>`.
- *Acceptance:* ≤2 open Dependabot PRs, none older than 14 days.

---

## PR C — Make `json_field` the supported path, not merely an available one (spec slug: `structured-acceptance-enforcement`)

spec-0032 shipped `json_field` for both `LoopNode.accept` and `BranchCase.when`
(analysis §1.1 proves the loop case executes today). What it did **not** ship is
any refusal of the wrong choice: `contains` over a `planner`/`reviewer` still
loads, and the mitigation was documentation. Milestone B1 of the plan of record
— making the predicate and `VALIDATE_OUTPUT` agree — never landed.

### Milestone C0 — refuse a text predicate bound to a structured agent

- **Failing test first:** `tests/test_workflow_loader.py::test_text_predicate_over_a_structured_agent_is_refused`
  — a `loop` over `reviewer` with `{"kind":"contains","value":"approved"}` must
  raise `ConfigError`. It loads clean today; that is the proof.
- **Depends on:** nothing — parallel-safe.
- Guard in `workflow/loader.py`, at the `ConfigError` boundary that already
  normalises every graph-shape failure, so the API surfaces 400 and the CLI
  exit-code 2 with no route or error-table change. **Not** in `graph.py`: the
  frozen models are reachable directly by library callers who may have a reason,
  and `errors.py` / the DTO surface stay untouched.
- Name the structured agents from one place. `StructuredOutputAgent` is the
  shared base for `planner`/`reviewer` (`agents/_structured.py`) — derive the set
  from the agent registry rather than restating a literal list, or a third
  structured agent inherits the hole.
- Two-sided proof per `mango-mutation-proof`: `json_field` over `reviewer` must
  still load, and `contains` over `chat` must still load. A one-sided test here
  passes a guard that refuses everything.

### Milestone C1 — make the two guards agree (plan-of-record B1)

- **Failing test first:** `tests/test_workflow_predicate.py::test_json_field_rejects_output_that_validate_output_would_reject`.
- **Depends on:** C0.
- This is milestone B1 of
  [`20260916T214636Z-reliability-evidence-plan.md`](20260916T214636Z-reliability-evidence-plan.md),
  restated here because it did not land with B0 and the plan still lists it
  outstanding. A `json_field` predicate on a structured agent must reject output
  that `MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT` would reject. `_parse_object`
  is already strict for this reason (`predicate/_client.py` R5 note) — the
  remaining gap is that the predicate never consults the parsed **model**, only
  the parsed mapping.
- Keep `AcceptanceFn` synchronous and total. The compiled closure must still
  never raise; a validation failure is "not accepted", so non-convergence keeps
  surfacing as `MaxStepsExceeded` rather than a new error type.

### Milestone C2 — make the flagship graph demonstrate the pattern

- **Failing test first:** `tests/test_run_workflow_e2e.py::test_shipped_example_graph_gates_on_a_parsed_field`
  — assert the shipped example carries a `json_field` acceptance. Red today:
  `examples/workflows/plan-execute-review.json` is a bare three-step `sequence`
  with no predicate anywhere.
- **Depends on:** C0, C1.
- Wrap the `reviewer` step in a `loop` whose `accept` is
  `{"kind":"json_field","field":"passed","equals":true}`, matching
  `ReviewResult`. The one graph this repo ships as an example should demonstrate
  the acceptance pattern the docs recommend; today it demonstrates no acceptance
  at all, which is why three consecutive reviews misread what it does.
- Keep `max_steps` low (2–3). The example is read far more often than it is run.

---

## PR D — Reconcile the release (spec slug: none — D10/D11/D12 execution)

`pyproject.toml:7` says `0.4.0`; the newest remote tag is `v0.1.0`;
`deploy.yml` fires on `release: published` and has therefore never executed.
spec-0024 is fully delivered. See analysis §1.5 and D10–D12.

### Milestone D0 — `main` disposition ⛔ blocked on D10

- **Depends on:** **D10** (sponsor). Cannot proceed without it.
- Delete or reconcile `main` (11 ahead / 263 behind). The 11 commits are a
  superseded lineage — the v0.4.0 cut, spec-0005 workflow graphs,
  `eval_harness_bridge`, enterprise hooks — whose content all exists on the
  default branch. Record the rationale in the D-register either way.

### Milestone D1 — rename the default branch ⛔ blocked on D11

- **Depends on:** D0 (the name must be free), **D11** (sponsor).
- `feat/initial-release` → `main`. Re-point branch protection (**and take D1
  from the 2026-08-22 register while in that settings page** — making
  `protected-paths`, `frontmatter` and `gate` required status checks is the same
  screen and has been the cheapest open unblock for 28 days).

### Milestone D2 — cut v0.4.0 ⛔ blocked on D12

- **Failing test first:** none — release execution. The acceptance is that
  `deploy.yml` runs end-to-end for the first time, including the spec-0024 smoke
  step.
- **Depends on:** D1, **D12** (sponsor), and **D2 from the 2026-08-22 register**
  (a named GCP project) for the deploy half. Without D2 the tag and `verify` job
  still run; the deploy step cannot.
- Roll `CHANGELOG.md`'s `[Unreleased]` into `## [0.4.0]`, tag from the renamed
  default branch, publish the release. Per `mango-release`.
- The version string already reads `0.4.0`, so this is a tag-and-changelog cut,
  not a bump. Do not bump to 0.5.0 to "make room" — that compounds the drift
  this milestone exists to remove.

---

## Deferred / out of scope

- **Everything in `20260916T214636Z-reliability-evidence-plan.md`.** PR A
  (`guard-probe`, `mutmut`, floor freeze) and PR C (behavioural gate, `pass^k`,
  `llm_judge` demotion) remain the main line and resume after PR B here. This
  plan does not restate or re-sequence them.
- **`mutmut` scoped to the 100 %-floor packages.** Considered and rejected: the
  plan of record's A1 scopes it to `core`, `workflow`, `composition` — where
  line coverage is least informative. Packages already at 100 % are where
  mutation testing is cheapest, not where it discriminates. Re-opening this
  must revisit A1's reasoning.
- **`MANGOMAS_EVAL__MAX_MEAN_COST_USD` / `cost_budget` wiring.** Belongs to the
  plan of record's PR C, and against the right harness: `eval-gate.yml` drives
  the external `ianshank/Agents` bridge, not `mangomas.eval` (analysis §1.2).
- **A Dependabot `groups:` entry spanning ecosystems.** Not expressible — groups
  are scoped to one `updates:` entry. Recorded at `.github/dependabot.yml:30-32`.
- **Durable execution, tool-result masking, compaction ledger, Sonar migration,
  MoE bake-off, METR horizon publication.** Rejected; each belongs to
  `ianshank/Mango_Code_Agent-Harness` or re-couples a boundary ADR-0029 / INV-16
  decoupled deliberately.
- **A workflow run ledger.** Still unallocated an ADR number, per
  `20260916T140000Z-governance-hardening-plan.md`. Not this plan's scope.

## Verification

```bash
make gate                                    # full pre-PR chain, in CI's order
python -m pytest tests/deploy -q             # PR A
python -m pytest tests/tooling -q            # PR B
python -m pytest tests/test_workflow_predicate.py tests/test_workflow_loader.py \
                tests/test_run_workflow_e2e.py -q   # PR C
make pip-audit                               # PR A1 — expect findings on first run
```

Baseline at `92d5e9d`, executed: `make gate` green; `python -m pytest` =
**2935 passed, 66 skipped**. Every milestone above starts from that.
