# Toolchain and acceptance parity — delivery plan

- **Branch:** `claude/council-review-replan-vaz9rp` (plan + analysis only); one branch per PR block below
- **Date:** 2026-09-19
- **Revision:** second pass. The first pass had four PR blocks; this one has
  five. PR C is **redesigned** — its original milestone restated the plan of
  record's B1, which analysis §N4 shows is not implementable as written — and
  PR D (cost honesty) is new.
- **Target release:** rolling; PR E is a precondition for the v0.4.0 cut (D12)
- **Status:** In progress — PRs A, B0, C and D0 delivered; B1, D1a/D1b and PR E blocked (see Status below)
- **Specs:** `spec-0034` (PR C, **written** — `specs/0034-structured-acceptance-enforcement.md`).
  The others are still unwritten and deliberately **unnumbered**: each PR block
  names the spec it needs by slug, and it takes the next free integer at the
  moment it is created, per `CLAUDE.md` § Spec-Driven Development. A reserved
  number that drifts is worse than no number at all — the governance-hardening
  plan lost `spec-0032`/`spec-0033` to a concurrent branch exactly that way.
  **Process note:** PR C's code was pushed before its spec, which
  `CLAUDE.md` § Spec-Driven Development does not permit. Caught in review
  (Copilot, PR #76) and corrected by writing `spec-0034` against the delivered
  behaviour. The spec is therefore a record rather than a design document, which
  is the weaker of the two — noted so the sequencing is not repeated.
- **ADRs:** one owed. PR C changes the `load_workflow` signature and PR D may
  change the `Target` seam; both are additive, but the `Target` change in D1b is
  a protocol evolution and needs a record if taken. D13 / D15 are register
  decisions, not ADRs.
- **Source:** [`docs/analysis/20260919-council-rejection-and-replan.md`](../analysis/20260919-council-rejection-and-replan.md)
- **Relationship to the plan of record:**
  [`20260916T214636Z-reliability-evidence-plan.md`](20260916T214636Z-reliability-evidence-plan.md)
  is unchanged and remains the spine. This plan holds only what that plan does
  not cover, plus the two corrections analysis §5 records against it (B1 is not
  implementable as written; PR C must settle cost semantics before setting a
  threshold).

## Status — 2026-09-19

**Delivered:** PR A in full (A0–A2), PR B milestone B0, PR C in full (C0–C3),
PR D milestone D0. `make gate` green at the end (exit 0): **3014 passed, 66
skipped**, every coverage floor met, and the four new/changed source modules at
**100 %** line and branch coverage.

**Outstanding, and why:**

- **B1 (drain the Dependabot queue)** — not started. It is PR-management work
  against GitHub, and the `github` MCP server was unavailable for most of this
  session. B0 landed first, which was the ordering constraint that mattered:
  the guard is now in place before any of those PRs can merge.
- **D1a / D1b (cost semantics)** — blocked on **D15**, as planned. D0 landed and
  is what makes that decision informed.
- **PR E (release reconciliation)** — blocked on **D10/D11/D12**, all sponsor
  decisions requiring repository-admin actions no file in this repo can express.

**Two deviations from the plan as written**, both taken deliberately and both
recorded in the milestones below:

1. **C1/C2 apply to `loop.accept` only, not to `branch.when`.** The plan said to
   walk both. That is wrong: `BranchNodeExecutor` evaluates `when` against the
   node's *input* content, so there is no agent whose schema it could be bound
   to. Guessing the upstream producer would produce false refusals.
2. **C3 adds a second example rather than changing the shipped one.** The plan
   said to wrap `plan-execute-review.json`'s reviewer step in a loop. Its
   all-`agent` shape is the `dispatch_pipeline` parity proof that four suites
   depend on, two of them gated and unrunnable here, so changing it would
   destroy a real demonstration to make a point better made additively.

---

## Executive summary

Five PRs. The sequencing constraint is **what each one protects**: PR A guards
the artefact that reaches production, so it goes first despite being the least
interesting; PR B has a hard deadline — it must land before the Dependabot queue
is drained or the guard arrives red behind four merged PRs; PR C closes the
acceptance hole that `json_field` shipped without, and is the only block with a
signature change; PR D settles what "cost" means before the plan of record puts
a threshold on it; PR E reconciles the release numbering so `deploy.yml` can
execute for the first time. Then the plan of record's PR A (`make guard-probe`)
resumes as the main line.

Every milestone here closes the same omission: a capability was built and the
constraint that makes it correct was written as prose. That is why each one is a
test or a guard, and why none of them is a redesign.

---

## PR A — Gate what production installs (spec slug: `lockfile-integrity`)

`requirements.lock` is the constraints file the runtime image installs through
(`Dockerfile:45-47`). It holds 32 `==` pins, is `pip-compile`-generated, and is
referenced nowhere in CI or the `Makefile`. See analysis §N2.

**Blocked on D13** for the rename half only. A0–A2 are correct under either
disposition and need no decision.

### Milestone A0 — the lock must match pyproject ✅

- **Failing test first:** `tests/deploy/test_lockfile_freshness.py::test_every_runtime_distribution_is_pinned_in_the_lockfile`
  — parse `[project].dependencies` names, assert each appears as an `==` pin in
  `requirements.lock`. Prove it two-sided per `mango-mutation-proof`: with a
  synthetic runtime dependency added to a `tmp_path` pyproject copy the check
  must fail; without it, pass. A one-sided version passes against an empty
  lockfile with zero names parsed — the silent-pass shape this repo has hit four
  times.
- **Depends on:** nothing — parallel-safe.
- Normalise distribution **names** per PEP 503 (`_ . -` → `-`, case-folded).
  `opentelemetry-api` vs `opentelemetry_api` is the first thing that bites.
- Compare names only, never specifiers. The lock pins the closure; pyproject
  states ranges. Asserting version agreement would fail a correctly generated
  lock.
- Extras are out of scope: the lock is generated `--strip-extras`, so
  `uvicorn[standard]` resolves to `uvicorn` plus its closure. Assert the base
  name and say so in the docstring.

### Milestone A1 — audit what ships, not only what resolves ✅

- **Failing test first:** `tests/deploy/test_ci_make_parity.py::test_pip_audit_covers_the_runtime_lockfile`
  — the `pip-audit` target must name `requirements.lock`. Red today.
- **Depends on:** A0 (auditing a stale lock reports on versions nobody ships).
- **This reverses a recorded decision, so state the reason in the commit.**
  `Makefile:210-213` argues the environment audit is sufficient because it "is
  what CI actually tests and what the runtime wheel resolves against, and it
  covers the dev pins and extras a runtime-only lockfile audit would never see."
  The second clause is correct — **keep the environment audit**. The first is
  false: `Dockerfile:47` passes `-c /tmp/requirements.lock`, so the runtime
  wheel resolves against the lock. Add `pip-audit -r requirements.lock` as a
  second invocation in the same target, per the one-place rule
  `test_ci_make_parity.py` enforces, and rewrite that comment to describe two
  surfaces rather than one.
- Expect red on first run. A CVE in the pinned closure is the finding, not a
  failure of the milestone. Fix by regenerating the lock, never by narrowing the
  audit.

### Milestone A2 — correct the claim the test makes ✅

- **Failing test first:** none. This is a docstring and comment correction, and
  a test asserting a docstring's wording would be the same defect in a new
  place.
- **Depends on:** A0, A1 (the claim becomes true once they land).
- `tests/deploy/test_workflow_hardening.py:181-189` documents
  `test_dependabot_covers_actions_and_python_ecosystems` as holding because "the
  `requirements.lock` pins and pyproject ranges rot without `pip`." It verifies
  that three ecosystems are configured; it cannot verify that, and per analysis
  §N2(2) the effect does not occur. Rewrite the docstring to what it checks and
  drop the freshness claim, which A0 now enforces for real. Same for
  `.github/dependabot.yml:11-14`.
- ⚠️ If D13 chooses the rename (`requirements.lock` → `requirements.txt`, so
  Dependabot parses it), this milestone lands it: `Dockerfile`, the three
  `tests/deploy/` references, and the file's own generation header. Hold the
  rename for D13; A0 and A1 do not wait.

---

## PR B — Close the toolchain parity hole (spec slug: `hook-dependency-parity`)

Two parity tests exist and neither covers the eight entries most likely to
drift. See analysis §N1.

**Hard ordering constraint: B0 lands before B1.** Reversed, the four
`dependabot/pre_commit/*` PRs merge clean and the guard arrives red behind them.

### Milestone B0 — the hook's dependency floors must not exceed runtime's ✅

- **Failing test first:** `tests/tooling/test_toolchain_pin_parity.py::test_mypy_hook_dependencies_agree_with_runtime_ranges`
  — for each `additional_dependencies` entry naming a distribution in
  `[project].dependencies`, the hook's specifier must equal the runtime
  specifier. Green against today's tree, so prove it on a fixture: a `tmp_path`
  pair with hook `starlette>=1.6.0` against pyproject `>=0.40` must fail.
- **Depends on:** nothing — parallel-safe. See the ordering constraint above.
- **`starlette` is the case that decides the design.** It is in the hook
  (`.pre-commit-config.yaml:36`) but **not** in `[project].dependencies` — it
  arrives transitively via `fastapi`. A naive `if name in pyproject` check skips
  it silently, which is how it drifted in the first place. Choose explicitly and
  record it in the docstring: either assert transitive-only entries against the
  lockfile pin (A0 makes that reliable, creating a soft dependency on PR A), or
  exempt them by name with the reason stated. Do not let them fall through.
- Grow the contract the way `TOOL_PINS` does — a module-level tuple, no
  distribution names in the test body.

### Milestone B1 — drain the queue

- **Failing test first:** none — queue work, gated by the existing suite plus
  B0.
- **Depends on:** B0.
- **`ruff` needs one commit, not two.** `dependabot/pip/ruff-0.16.7`
  (`pyproject.toml:45`) and
  `dependabot/pre_commit/https-/github.com/astral-sh/ruff-pre-commit-0.16.7`
  (`.pre-commit-config.yaml:6`) each fail `test_toolchain_pin_parity.py` alone.
  Land one branch bumping both, then close both Dependabot PRs as superseded.
  Same shape for `mypy` (`pyproject.toml:48` + `.pre-commit-config.yaml:23`).
- **The four `pre_commit` runtime-name PRs are typecheck-scope changes**, each
  editing one `additional_dependencies` line. Under B0 they are mergeable only
  alongside the matching pyproject range change. Treat each as "do we want to
  support this range?", decide per distribution, close the rest.
- **CI-action majors batch.** The five `github_actions` branches land as one
  batch behind the existing SHA-pin policy and
  `tests/deploy/test_workflow_hardening.py`'s first-party/third-party split.
- ⚠️ `pyproject.toml` is a protected path (ADR-0030). Every commit touching it
  needs a trailer; prefer the path-scoped form
  `BREAKING-CHANGE: pyproject.toml — <rationale>`.
- *Acceptance:* ≤2 open Dependabot PRs, none older than 14 days.

---

## PR C — Make `json_field` safe to recommend (spec slug: `structured-acceptance-enforcement`)

spec-0032 shipped `json_field` for both `LoopNode.accept` and `BranchCase.when`
(analysis §1.1 proves the loop case executes today). It shipped without two
guards: nothing refuses `contains` over a structured agent, and nothing checks
that a `json_field` path resolves against anything. Adopting the documented
recommendation therefore trades a silent-acceptance bug for a silent
never-accepts bug (analysis §N4).

**This block supersedes the plan of record's milestone B1**, which asks the
predicate to agree with `VALIDATE_OUTPUT` and is not implementable as written:
the predicate compiles from pure data at load time with no agent instance, no
schema, and — under the `workflow`/`eval`/`rag`/`cognitive` independence
contract — no business importing `agents/`.

### Milestone C0 — make the structured-agent set derivable, not restated ✅

- **Failing test first:** `tests/composition/test_agents.py::test_a_new_structured_agent_is_covered_by_the_table_alone` (delivered; the plan's earlier draft named `test_structured_agent_names_are_derived_from_the_registration_table`)
  — a sixth agent added to the table that subclasses `StructuredOutputAgent`
  must appear in the exported set without editing a literal list.
- **Depends on:** nothing — parallel-safe, and the enabler for C1/C2.
- `composition/agents.py:23-27` holds the name→class mapping inside five
  lambdas. Turn it into a module-level table
  (`{"chat": ChatAgent, …}`) and derive
  `STRUCTURED_AGENT_SCHEMAS: Mapping[str, type[BaseModel]]` from
  `issubclass(cls, StructuredOutputAgent)`. Registration keeps its current
  shape, built from the table.
- **The schema is not reachable from the class today, so this milestone adds
  that.** `PlannerAgent.__init__` passes its model *positionally* —
  `super().__init__(ExecutionPlan, "planner", …)` (`agents/planner.py:48`;
  `agents/reviewer.py:42` likewise) — and `StructuredOutputAgent` stores it as the
  private `self._schema` (`agents/_structured.py:68`). Declare
  `schema: ClassVar[type[BaseModel]] = ExecutionPlan` / `= ReviewResult` on the
  two subclasses and pass `type(self).schema` to `super().__init__`, which keeps
  the positional parameter and every existing caller working. Composition then
  derives the mapping via `getattr(cls, "schema", None)`.
- Do not instantiate to read the schema, and do not touch `_schema` from
  outside. `PlannerAgent()` happens to construct with both arguments defaulted,
  which makes the shortcut tempting and wrong — it builds a prompt and resolves
  sampling settings as a side effect of a lookup.
- `composition/` is not a protected path. No trailer.
- **Known limit, state it in the docstring:** entry-point discovered agents are
  registered after composition and are not in the table, so a discovered
  structured agent gets no guard. Record it rather than implying coverage.

### Milestone C1 — refuse a text predicate over a structured agent ✅

- **Failing test first:** `tests/test_workflow_validation.py::test_text_predicate_over_a_structured_agent_is_refused` (delivered in the new `workflow/validation.py`'s own module, not the loader's)
  — a `loop` over `reviewer` with `{"kind":"contains","value":"approved"}` must
  raise `ConfigError`. It loads clean today; that is the proof.
- **Depends on:** C0.
- **Signature, additive:**
  `load_workflow(source, *, structured_agents: Mapping[str, type[BaseModel]] | None = None)`.
  The default preserves all three existing call sites byte-for-byte
  (`api/routes/workflows.py:51,62`, `cli/commands/workflow.py:51`), then each
  passes `STRUCTURED_AGENT_SCHEMAS`. Passing the set **as data** is what keeps
  `workflow/` pure — it must not import `agents/`, and the independence contract
  does not catch that import, so the discipline has to be deliberate.
- Guard in `workflow/loader.py`, at the `ConfigError` boundary that already
  normalises every graph-shape failure — so the API returns 400 and the CLI
  exits 2 with no route, DTO, or `_ERROR_STATUS` change. Not in `graph.py`: the
  frozen models are reachable directly by library callers who may have reason.
- Walk `loop.accept` **and** every `branch.when`; both are `PredicateSpec` and
  both have the defect.
- Two-sided proof: `json_field` over `reviewer` must still load, and `contains`
  over `chat` must still load. A one-sided test passes a guard that refuses
  everything.
- **Highest-value call site:** `POST /workflows/run` accepts a caller-supplied
  inline graph and runs it **even when the feature is disabled**
  (`api/routes/workflows.py:40-51`). That is where an external caller can post a
  loop that accepts on a substring, so the route must pass the set.

### Milestone C2 — refuse a `json_field` path that cannot resolve ✅

- **Failing test first:** `tests/test_workflow_validation.py::test_json_field_path_absent_from_the_agent_schema_is_refused`, plus `::test_a_dotted_path_through_a_non_object_field_is_refused` (added after review — see Milestone C2)
  — `field: "pased"` over `reviewer` must raise `ConfigError`. Probed loading
  clean today and returning `False` for every response.
- **Depends on:** C1 (same parameter, same walk).
- Validate the path's first segment against the agent's
  `model_json_schema()["properties"]`.
- **Amended after review.** The first draft validated *only* the first segment and
  a test asserted that `steps.0.action` therefore loads. That blessed exactly the
  never-accepts defect this milestone exists to prevent: `ExecutionPlan.steps` is
  an array and `predicate._resolve` walks mappings only, so the path can never
  resolve. The schema summary now also carries which fields are **objects**, and a
  dotted path through a non-object is refused. Both shipped models are flat, so
  every dotted path over them is refused — correctly. Segments beyond the second
  remain unvalidated, which needs the nested model's schema.
- This is the achievable form of plan-of-record B1: it makes the predicate and
  the schema agree, without the predicate importing an agent. Say so in the spec
  so B1 is closed by reference rather than left dangling.
- Leave `compile_predicate` **total**. Runtime behaviour does not change; a
  path that fails to resolve at run time is still "not accepted", so
  non-convergence keeps surfacing as `MaxStepsExceeded`. The new refusal is
  load-time only.

### Milestone C3 — make the shipped example demonstrate the pattern ✅

- **Failing test first:** `tests/test_plan_review_until_passed.py::test_example_graph_gates_on_a_parsed_field` (delivered as its own module beside the parity example's)
  — assert the example carries a `json_field` acceptance. Red today:
  `examples/workflows/plan-execute-review.json` is a bare three-step `sequence`
  with no predicate anywhere.
- **Depends on:** C1, C2.
- Wrap the `reviewer` step in a `loop` whose `accept` is
  `{"kind":"json_field","field":"passed","equals":true}`, matching
  `ReviewResult`'s actual property. Keep `max_steps` at 2–3; the example is read
  far more than it is run.
- The one graph this repo ships demonstrates no acceptance at all, which is why
  three consecutive reviews misread what it does. Fixing the example is the
  cheapest correction to that whole class of error.

---

## PR D — Say what cost measures (spec slug: `eval-cost-semantics`)

`mean_cost_usd`, `MANGOMAS_EVAL__MAX_MEAN_COST_USD` and `cost_budget` all exist
and are tested. No path carries a run's actual consumption into them:
`Target.run` returns `str` (`eval/target.py:31`), so `AgentResponse.metadata` is
discarded, and `ScorerContext.row_metadata` is the **dataset's** metadata. See
analysis §N3.

**Blocked on D15** for the choice between D1a and D1b. D0 is correct either way
and is what makes the choice informed.

### Milestone D0 — pin the current semantics before changing anything ✅

- **Failing test first:** `tests/eval/test_cost_measurement_basis.py::test_cost_is_blind_to_the_model_the_run_used` (delivered as its own module; the fixture builds a real `agent_llm_overrides` swap, per review)
  — a row with no declared token counts must produce the **same**
  `mean_cost_usd` under two different model configurations, and a different one
  when only the reply's length changes. That is the current contract, and
  nothing states it.
- **Depends on:** nothing — parallel-safe.
- Document the fall-through in `docs/eval/harness.md` and the `mango-eval`
  skill: with no declared `cost_usd` or token counts, cost is
  characters × rate. So a `mean_cost_usd` gate fires on **verbosity** and is
  blind to a costlier model via `MODEL_OVERRIDE`, a pricier token, or extra tool
  steps.
- `tests/eval/fixtures/cost_controlled_v1.jsonl` hand-declares token counts and
  `test_cost_controlled_dataset.py` exercises it across `echo`/`agent`/`pipeline`.
  That design is sound — a fixed declared budget for comparing targets — and
  this milestone does not disturb it. It names the limit, which is the part
  missing.

### Milestone D1a — the honest interim ⛔ blocked on D15

- **Failing test first:** `tests/eval/test_gate.py::test_cost_gate_names_its_measurement_basis`.
- **Depends on:** D0, **D15** (sponsor).
- If D15 chooses declared-cost: keep the seam as built, and make the
  `cost_budget` report and the gate's failure message state the basis
  (`source=output_chars` vs `tokens` vs `explicit`) so no one reads a
  character-proxy figure as spend. `estimate_cost_usd` already returns that
  `source`; the gate discards it.
- Under this option the plan of record's PR C may gate `mean_cost_usd` **only**
  over a declared-cost cohort, and `docs/testing/regression.md` must say so next
  to the threshold.

### Milestone D1b — measured cost ⛔ blocked on D15

- **Failing test first:** `tests/eval/test_runner.py::test_response_metadata_reaches_the_scorer_context`.
- **Depends on:** D0, **D15** (sponsor), and adapter token telemetry, which does
  not exist — `grep -rn "usage\|prompt_tokens\|completion_tokens" src/mangomas/adapters/llm/ src/mangomas/core/`
  finds only Vertex's `max_output_tokens` request field. That is the real cost of
  this option and the reason it is not the default recommendation.
- Two changes, both additive. (1) `LLMClient` implementations surface provider
  `usage` on `AgentResponse.metadata`. (2) The runner reads response metadata
  into `ScorerContext`. Do **not** change `Target.run`'s return type — it is
  `@runtime_checkable` and third-party targets implement it. Add a separate
  optional protocol the runner probes with `isinstance`, matching how this repo
  handles `PingableLLMClient` / `StreamingLLMClient`.
- Needs an ADR: a new protocol on the eval seam plus a metadata contract on
  `AgentResponse`. `core/agent.py` is a protected path — if `AgentResponse`
  itself needs a field, that is a trailer and `mango-schema-evolution`'s call,
  not this plan's.

---

## PR E — Reconcile the release (spec slug: none — D10/D11/D12 execution)

`pyproject.toml:7` says `0.4.0`; the newest remote tag is `v0.1.0`; `deploy.yml`
fires on `release: published` and has therefore never executed. spec-0024 is
fully delivered. See analysis §1.5 and D10–D12.

### Milestone E0 — `main` disposition ⛔ blocked on D10

- **Depends on:** **D10** (sponsor). Cannot proceed without it.
- Delete or reconcile `main` (11 ahead / 263 behind). The 11 commits are a
  superseded lineage — the v0.4.0 cut, spec-0005 workflow graphs,
  `eval_harness_bridge`, enterprise hooks — whose content all exists on the
  default branch. Record the rationale either way.

### Milestone E1 — rename the default branch ⛔ blocked on D11

- **Depends on:** E0 (the name must be free), **D11** (sponsor).
- `feat/initial-release` → `main`, and re-point branch protection. **Take D1
  from the 2026-08-22 register in the same settings visit** — making
  `protected-paths`, `frontmatter` and `gate` required status checks is the same
  screen and has been the cheapest open unblock for 28 days.

### Milestone E2 — cut v0.4.0 ⛔ blocked on D12

- **Failing test first:** none — release execution. The acceptance is
  `deploy.yml` running end-to-end for the first time, including the spec-0024
  smoke step.
- **Depends on:** E1, **D12** (sponsor), and **D2 from the 2026-08-22 register**
  (a named GCP project) for the deploy half. Without D2 the tag and the `verify`
  job still run; the deploy step cannot.
- Roll `CHANGELOG.md`'s `[Unreleased]` into `## [0.4.0]`, tag from the renamed
  default branch, publish. Per `mango-release`.
- The version string already reads `0.4.0`, so this is a tag-and-changelog cut,
  not a bump. Do not bump to 0.5.0 to "make room" — that compounds the drift
  this milestone removes.

---

## Deferred / out of scope

- **Everything in `20260916T214636Z-reliability-evidence-plan.md`.** Its PR A
  (`guard-probe`, `mutmut`, floor freeze) and PR C (behavioural gate, `pass^k`,
  `llm_judge` demotion) remain the main line and resume after PR B here. The
  only edits this plan makes to it are recorded in analysis §5: B1 superseded by
  PR C above, PR C's cost threshold gated on D15, two milestones sequenced
  ahead, and A2's stale floor count.
- **`mutmut` scoped to the 100 %-floor packages.** Considered and rejected: the
  plan of record's A1 scopes it to `core`, `workflow`, `composition` — where line
  coverage is least informative. Packages already at 100 % are where mutation
  testing is cheapest, not where it discriminates. Re-opening must revisit A1.
- **Changing `Target.run`'s return type.** Rejected even under D1b: the protocol
  is `@runtime_checkable` and third-party targets implement it. An optional
  companion protocol is the additive route.
- **Nested-path schema validation in C2.** First segment only. `$ref`/`anyOf`
  resolution buys nothing for two flat top-level models.
- **A Dependabot `groups:` entry spanning ecosystems.** Not expressible — groups
  are scoped to one `updates:` entry. Recorded at `.github/dependabot.yml:30-32`.
- **Durable execution, tool-result masking, compaction ledger, Sonar migration,
  MoE bake-off, METR horizon publication.** Rejected; each belongs to
  `ianshank/Mango_Code_Agent-Harness` or re-couples a boundary ADR-0029 / INV-16
  decoupled deliberately.
- **A workflow run ledger.** Still has no ADR number allocated, per
  `20260916T140000Z-governance-hardening-plan.md`. Not this plan's scope.

## Verification

```bash
make gate                                    # full pre-PR chain, in CI's order
python -m pytest tests/deploy -q             # PR A
python -m pytest tests/tooling -q            # PR B
python -m pytest tests/composition tests/test_workflow_loader.py \
                tests/test_workflow_predicate.py \
                tests/test_run_workflow_e2e.py -q          # PR C
python -m pytest tests/eval -q               # PR D
make pip-audit                               # PR A1 — expect findings on first run
```

Baseline at `92d5e9d`, executed: `make gate` green; `python -m pytest` =
**2935 passed, 66 skipped**. Every milestone above starts from that.
