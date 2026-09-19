# Council rejection and replan — source-verified (2026-09-19)

- **Scope:** `Mango-Metrics-NLM/MangoMas_V2` @ `92d5e9d` (default branch
  `feat/initial-release`), package `mangomas`, plus `eval_harness_bridge/` and
  `mango-integration-contracts/`.
- **Method:** full local checkout, dependencies installed, **suite and gate
  executed**. `make gate` green; `python -m pytest` = **2935 passed, 66
  skipped** at `92d5e9d`. Predicate claims were probed with a script, not read
  off the source. Every verdict cites a file, a line, or a command output.
- **Subject:** a three-model council review of the next development cycle, and
  the five-finding rejection written against it.
- **Plan:** [`docs/plans/20260919T000000Z-toolchain-and-acceptance-parity-plan.md`](../plans/20260919T000000Z-toolchain-and-acceptance-parity-plan.md).
- **Revision:** second pass (2026-09-19). The first pass carried findings N1–N2;
  this pass adds **N3** and **N4** from a deeper read of the `eval` target seam
  and the predicate compiler, corrects N1's scope against
  `tests/tooling/test_precommit_parity.py`, and rebuts the recorded decision
  that argues against N2's remedy. §1 is unchanged — nothing in the second pass
  disturbed it.
- **Relationship to prior work:** this document does **not** supersede
  [`20260916-council-roadmap-adjudication.md`](20260916-council-roadmap-adjudication.md)
  or [`20260916T214636Z-reliability-evidence-plan.md`](../plans/20260916T214636Z-reliability-evidence-plan.md).
  It confirms that the plan of record already covers most of what was proposed
  as new, adds the four findings nothing in the tree covers, and records two
  corrections against that plan (§5).

---

## 0. The council document

**Rejected, and the rejection is correct on every point I could check.**

| Rejection claim | Verification |
|---|---|
| README already disambiguates the demo, the MoE checkpoint, OFFIS and mangometrics.io | `README.md:302-307` — names `MangoMas-Demo`, `MangoMAS-MoE-7M`, OFFIS `mango-agents`, `mangometrics.io` explicitly |
| `routing.recommendation` is already a reserved schema, not a grant | `README.md:309`, `docs/adr/0029-cognitive-execution-boundary.md:49` |
| This is a repeat offence | `docs/analysis/20260912-council-peer-review-rewrite.md` exists for the same failure |
| `.mcp.json` ships six servers, no Perplexity | `json.load` → `['fetch','filesystem','git','github','repomix','sequential-thinking']` |
| Durable execution belongs to the sibling repo | ADR-0029 / INV-16; `20260916-workflow-governance-audit.md` §1 confirms no run state, **by scope decision** |

The process finding — require a fetch receipt before admitting a finding — is
the right corrective and is the one control this repo applies internally but
not to its own review inputs.

---

## 1. The rejection's own five findings, adjudicated

The rejection is a better document than the thing it rejects. It is also
**not itself source-verified**, and four of its five findings need correction.

### 1.1 Finding 1 — "self-grading acceptance loop" · **Refuted**

The claim: `branch` got `json_field` (spec-0032 / ADR-0034); `loop` didn't, so
the flagship `plan-execute-review.json` graph terminates on a substring match
over the reviewer's own JSON.

Three independent refutations:

1. **`loop` and `branch` share one predicate type.** `LoopNode.accept` and
   `BranchCase.when` are both `PredicateSpec` (`workflow/graph.py:55`,
   `workflow/graph.py:62`), and `LoopNodeExecutor.__init__` calls the identical
   `compile_predicate` (`workflow/nodes/loop.py:27`) that `branch` calls
   (`workflow/nodes/branch.py:38`). There is no per-node kind restriction.
2. **spec-0032 says so in writing.** `specs/0032-structured-acceptance-predicates.md:121`
   — the new kind is "reached through `LoopNode.accept` **and**
   `BranchCase.when`". Both were in scope and both shipped.
3. **Executed proof.** A `loop` node carrying
   `{"kind":"json_field","field":"passed","equals":true}` loads today. And the
   rejection's own adversarial fixture behaves as the *already-fixed* case:

   ```
   loop+json_field loads OK -> loop json_field passed
   contains:'approved' on rejecting review -> True   (the real defect)
   json_field verdict==approve             -> False  (already fixed)
   ```

`examples/workflows/plan-execute-review.json` also contains **no `loop` node at
all** — it is a three-step `sequence` of `planner` → `tool` → `reviewer` with no
acceptance predicate anywhere. The specific mechanism described does not exist
in that file.

**The real residual, which is smaller and cheaper.** [Certain] Nothing *refuses*
a text predicate bound to a structured agent. `json_field` is available;
choosing `contains` over a `planner`/`reviewer` is still permitted, and
`workflow/loader.py` validates JSON and schema only. Spec-0032's mitigation was
documentation (`docs/workflow/graphs.md`, the `mango-workflow` skill) — prose,
which is the defect class this repo names in spec-0022 R15. Additionally
**PR B1 never landed**: `tests/workflow/` does not exist and no test named
`test_json_field_rejects_output_that_validate_output_would_reject` is present,
so the `json_field` predicate and `VALIDATE_OUTPUT` still cannot be shown to
agree. That is the honest version of Finding 1, and it is a loader guard plus
one test — not a new predicate kind.

### 1.2 Finding 2 — "gates measure structure" · **Confirmed, and three days old**

Every bullet checks out: `eval-gate.yml` is `workflow_dispatch` only,
`if: vars.EVAL_GATE_ENABLED == 'true'`, five dataset rows
(`wc -l eval_harness_bridge/datasets/mango_suite.jsonl` = 5), one active scorer
(`exact_match`), gate rule `pass_rate >= 0.5`
(`eval_harness_bridge/config/mangomas.eval.yaml`), one agent
(`MANGO_AGENT || 'chat'`). Mutation-proof is prose in
`.claude/skills/mango-mutation-proof/SKILL.md` and `specs/TEMPLATE.md` with
nothing mechanising it.

But `20260916-council-roadmap-adjudication.md` §2.1 and §2.2 state all of this,
in the same order, three days earlier — and draw the sharper conclusion the
rejection omits: **the work is promotion, not construction**, because the
bridge, converter, dataset shape, readiness probe and artefact upload already
exist and are unit-covered at a 100 % floor (`tests/eval_harness_bridge/`).

Two corrections to the rejection's prescription:

- **Wrong harness.** [Certain] `eval-gate.yml` drives the *external*
  `ianshank/Agents` harness through `eval_harness_bridge`, not
  `mangomas.eval`. So "wire `cost_budget` + `MANGOMAS_EVAL__MAX_MEAN_COST_USD`,
  both of which already exist" points at a second, different eval system that
  the gate does not run. Promoting the gate and wiring `mangomas.eval`'s cost
  budget are two separate pieces of work; conflating them will produce a
  threshold nobody can trace to a runner.
- **Nine floors, not seven.** [Certain] `scripts/check_coverage.py:43-71` holds
  23 floors, of which **nine** sit at 100 %: `errors`, `registry`, `core`,
  `secrets`, `correlation`, `tenancy`, `headers`, `utils`, `entry_points`. The
  2026-09-16 adjudication corrected an earlier council from seven to eight;
  `utils` has since been added. Scoping `mutmut` to those packages also inverts
  the plan of record's better reasoning — milestone A1 targets `core`,
  `workflow`, `composition`, "the three surfaces where a defect is most
  expensive and line coverage is least informative." Packages already at 100 %
  are where mutation testing is *cheapest*, not where it is most informative.

### 1.3 Finding 3 — "governance is advisory" · **Confirmed in-repo; branch protection unverified**

`protected-paths` exists as a CI job (`.github/workflows/ci.yml:95`) and as
`make protected-paths`. Whether it is a **required status check** is GitHub
branch-protection state, not repository content: **[Unverified this session]** —
the `github` MCP server failed to connect (`CONNECTION_CLOSED`), so I could not
read branch protection, PR, or issue state through it.

This finding is not new either. It is **D1** in
`docs/analysis/20260822-next-steps-roadmap-analysis.md:155`, open since
2026-08-22, recorded there as "the cheapest unblock in the register." Nearly a
month later it is still the cheapest unblock. Restating it as a new discovery
costs a cycle; **taking** it costs one settings change.

### 1.4 Finding 4 — "Dependabot deadlock" · **Count confirmed; diagnosis wrong; prescription impossible**

Fourteen Dependabot branches confirmed (`git ls-remote --heads origin | grep -c
dependabot` = 14). Everything after the count needs correcting.

**The "runtime majors" are not runtime changes.** [Certain] `starlette 1.6.0`,
`fastapi 0.141.1`, `httpx 0.28.1` and `typer 0.27.2` arrive on
`dependabot/pre_commit/*` branches. `git diff` on
`dependabot/pre_commit/starlette-1.6.0`:

```
 .pre-commit-config.yaml | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

One line, in the **mypy hook's `additional_dependencies`**
(`.pre-commit-config.yaml:28-36`). `pyproject.toml`'s runtime block is
untouched and still reads `fastapi>=0.115`, `httpx>=0.27`, `typer>=0.12`, with
no starlette pin at all. None of these four PRs can move `StreamingResponse` or
SSE semantics, so the stated risk to spec-0025's turn-persistence contract does
not arise from them. What they change is what mypy type-checks `src/` against —
a typecheck signal, on a different axis.

**The prescribed fix cannot be built.** [Certain] "Add a Dependabot `groups:`
entry binding ruff/mypy across the pip and pre-commit ecosystems" is not
expressible: `groups` is scoped within a single `updates:` entry, i.e. one
ecosystem and directory. `.github/dependabot.yml:30-32` already records this —
"Groups cannot span ecosystems, so the two PRs still arrive separately: merge
them together, or land one commit that bumps both files."

**The deadlock itself is real, and narrower than described.** `ruff 0.16.7`
arrives twice — `dependabot/pip/ruff-0.16.7` (touches `pyproject.toml`) and
`dependabot/pre_commit/https-/github.com/astral-sh/ruff-pre-commit-0.16.7`
(touches `.pre-commit-config.yaml`) — and
`tests/tooling/test_toolchain_pin_parity.py` fails whichever lands alone. That
is by design and documented. The remedy is a merge procedure or one reconciling
commit, not a config change.

### 1.5 Finding 5 — "default branch is a feature branch" · **Confirmed, with three corrections**

`main` exists and is **11 ahead / 263 behind** `feat/initial-release`
(`git rev-list --left-right --count`). So it is genuinely divergent, not merely
stale, and deleting it would discard 11 commits.

- **Those 11 commits are a superseded lineage, not lost work.** They are the
  v0.4.0 cut, spec-0005 workflow graphs, `eval_harness_bridge`, and the
  enterprise-hooks hardening — all of which have equivalents on the default
  branch (the bridge, the workflow package and ADR-0011 are all present). So
  "reconcile or delete" is defensible, but it is a history decision that needs
  a recorded rationale, not a casual deletion.
- **No `dag` node exists.** [Certain] `grep -rn '"dag"' src/ specs/ docs/`
  returns nothing. There is nothing to port.
- **spec-0024 is already delivered.** [Certain]
  `specs/0024-deploy-manifest-application.md:80-88` — every acceptance
  criterion is `[x]`, including `deploy.yml` applying `deploy/service.yaml`,
  the post-deploy `/healthz` + `/readyz` smoke, the two-directional contract
  test, and the `verify` job gating deploy (`deploy.yml:21-24`). The
  rejection's R5 presents finished work as the last step before release.

**The real release inconsistency, which the finding misses.** [Certain]
`pyproject.toml:7` reads `version = "0.4.0"`; the only tag on the remote is
`v0.1.0`; `CHANGELOG.md` carries a non-empty `[Unreleased]`. The version string
was bumped by `e4139d6 chore(release): cut v0.4.0` on the abandoned `main`
lineage and the default branch inherited the number without the tag. Since
`deploy.yml` fires on `release: published`, **the deploy pipeline has never
run** — it is contract-tested only, exactly as D2 predicted.

---

## 2. Findings nothing in the tree covers

These four are not in the council document, not in the rejection, and not in any
in-tree analysis or plan. All four are in the repo's own signature defect class —
a constraint asserted in prose while the mechanism does something narrower. In
N2 a **passing test** asserts the missing effect; in N3 a **protocol return
type** makes the asserted effect unreachable; in N4 the guard exists and is
simply never consulted.

N1 and N2 are supply chain and land first. N3 and N4 change what the plan of
record's behavioural-gate work can actually claim, so they are sequenced against
it rather than ahead of it.

### N1 — the mypy hook's `additional_dependencies` has no parity guard · High

`tests/tooling/test_toolchain_pin_parity.py` was written for exactly this
failure mode, and its `TOOL_PINS` tuple covers two things: `ruff` and `mypy`,
rev-versus-pin. There is a **second** parity test —
`tests/tooling/test_precommit_parity.py` — and it does not close the gap
either: it checks that the local hooks mirror the Makefile
(`_REQUIRED_LOCAL_HOOK_IDS`), that the `validate-config` hook covers the same
files as `make validate-config`, and that the `lint-imports` hook matches its
Makefile invocation. `grep -rn additional_dependencies tests/` returns
**nothing**. Two parity tests, neither covering the eight entries most likely
to drift.

So the eight entries under the mypy hook
(`.pre-commit-config.yaml:29-36`: `fastapi>=0.115`, `pydantic>=2.7`,
`pydantic-settings>=2.3`, `httpx>=0.27`, `opentelemetry-api>=1.25`,
`opentelemetry-sdk>=1.25`, `typer>=0.12`, `starlette>=0.40`) are free to drift
from `pyproject.toml`'s runtime ranges. The hook's own comment claims the list
"covers exactly the runtime deps" — that is the assertion with no mechanism
behind it. The consequence is the one the parity test's docstring already
describes, one level down: a contributor's pre-commit and CI disagree, and it
surfaces as a type error nobody else can reproduce.

**Correction to this finding's first pass.** [Certain] The first pass said the
four open `dependabot/pre_commit/*` PRs "each widen the gap", and reasoned that
`starlette>=1.6.0` in the hook would typecheck against a major the application
does not run because `fastapi>=0.115` caps starlette below 1.0. Measured against
the tree, that is wrong in both halves. `requirements.lock` — what the runtime
image installs — already pins `fastapi==0.141.1`, `httpx==0.28.1`,
`starlette==1.6.0` and `typer==0.27.1`. Every current hook floor *admits* its
locked version, and seven of the eight entries match pyproject's specifier
exactly. So the tree is consistent today, and those PRs would raise the floors
*towards* what ships, not away from it.

The finding survives because nothing enforces any of it. What changes is the
failure direction, and it is worth stating precisely because it decides how the
queue is drained: under the guard implemented in PR B, `starlette-1.6.0` is
**mergeable** (transitive-only, and its floor admits the locked version), while
`fastapi-0.141.1`, `httpx-0.28.1` and `typer-0.27.2` each **require a paired
`pyproject.toml` range bump**, because a direct entry must match the declared
range rather than merely admit the locked version. Verified by running the
implemented guard against each branch's actual `.pre-commit-config.yaml`:

```
starlette-1.6.0    -> clean (mergeable)
fastapi-0.141.1    -> CAUGHT: hook says '>=0.141.1', pyproject declares '>=0.115'
httpx-0.28.1       -> CAUGHT: hook says '>=0.28.1',  pyproject declares '>=0.27'
typer-0.27.2       -> CAUGHT: hook says '>=0.27.2',  pyproject declares '>=0.12'
```

### N2 — `requirements.lock` is unmanaged and unaudited, and a passing test implies otherwise · High

The lockfile is what the **production image installs**: `Dockerfile:45-47`
copies it and passes `-c /tmp/requirements.lock` to `pip install`. It holds 32
`==` pins of the transitive runtime closure and is generated by `pip-compile`.

Three gaps:

1. **Not audited, by a recorded decision whose stated reason is wrong.**
   `grep -n requirements.lock .github/workflows/*.yml Makefile` returns nothing.
   The `pip-audit` job (`ci.yml:192-212`) runs `make pip-audit`
   over the `make install` environment — resolved from pyproject's `>=` ranges,
   **not** the lock's pins. `Makefile:210-213` argues this is deliberate,
   because the installed environment "is what CI actually tests and what the
   runtime wheel resolves against, and it covers the dev pins and extras a
   runtime-only lockfile audit would never see."

   The second half is correct and is why the environment audit must stay. The
   first half is **false**: `Dockerfile:47` passes `-c /tmp/requirements.lock`,
   so the runtime wheel resolves against the **lock**, not the dev environment.
   The one artefact that reaches production is the one nothing scans. This is
   not an argument against the decision's caution — it is an argument that the
   decision was taken on an incorrect premise and covers one of two surfaces.
2. **Not updated.** [Likely] `git log -- requirements.lock` shows exactly one
   commit: the one that created it. None of the 14 open Dependabot branches
   touches it. Dependabot's `pip` ecosystem recognises `requirements.txt`,
   `requirements/*.txt`, `pyproject.toml`, `setup.py`, `Pipfile` and
   `poetry.lock` — a file named `requirements.**lock**` is not a manifest it
   parses. Marked [Likely] rather than [Certain] because this is upstream
   behaviour, not repository content; the evidence for it is the single-commit
   history under a configured pip ecosystem.
3. **Not checked for freshness.** Nothing compares the lock to pyproject. Add a
   runtime dependency without regenerating and the build still succeeds —
   constraints pin, they never add — so the new dependency floats while
   everything around it is pinned, with no signal.

And the assertion that hides this: `tests/deploy/test_workflow_hardening.py:185`
documents `test_dependabot_covers_actions_and_python_ecosystems` as holding
because "the `requirements.lock` pins and pyproject ranges rot without `pip`."
The test verifies that `pip` appears in the ecosystem set. It cannot verify the
effect its docstring claims, and per (2) that effect does not occur. A green
test asserting a control that is absent is worse than prose, because prose does
not read as verified.

### N3 — eval cost is declared in the dataset, never measured, so a cost gate cannot catch a cost regression · High

`MANGOMAS_EVAL__MAX_MEAN_COST_USD`, `mean_cost_usd` on `EvalReport`, the
`cost_budget` scorer and `estimate_cost_usd`'s three-tier precedence all exist
and are tested. What does not exist is any path by which a run's actual
consumption reaches them.

The chain, end to end:

1. `Target.run(request, *, orch) -> str` (`eval/target.py:31`). The signature
   returns a **string**. `AgentTarget.run` awaits `orch.dispatch(...)` and
   returns `response.content` (`eval/targets/agent.py:30`) — the
   `AgentResponse` and every field on it, `metadata` included, are discarded at
   this boundary.
2. `EvalRunner._score_row` builds `ScorerContext(row_metadata=dict(row.metadata))`
   (`eval/runner.py:165`). That is the **dataset row's** metadata — the input
   the JSONL file declared, not anything the run produced.
3. `CostBudgetScorer.score` reads `metadata = dict(context.row_metadata)` and
   passes it to `estimate_cost_usd`, whose precedence is explicit `cost_usd` →
   token counts → output-character rate.

So `cost_budget` can only ever see token counts a human typed into the dataset.
`tests/eval/fixtures/cost_controlled_v1.jsonl` does exactly that —
`{"metadata": {"input_tokens": 10, "output_tokens": 20, "cohort": "cost-controlled-v1"}}`
— which confirms the design is **deliberate**, not an oversight: it is a
fixed-budget cohort for comparing targets at equal declared cost, and
`tests/eval/test_cost_controlled_dataset.py` exercises `echo` / `agent` /
`pipeline` against it. That is a legitimate thing to have.

The defect is what the repository then claims. No adapter emits token usage at
all — `grep -rn "usage\|prompt_tokens\|completion_tokens" src/mangomas/adapters/llm/ src/mangomas/core/`
returns only Vertex's `max_output_tokens` **request** field — so for any dataset
that does not hand-declare counts, cost falls through to characters × a rate.
A gate on `mean_cost_usd` therefore fails when the model becomes **wordier**,
and passes unchanged when it becomes more expensive per token, switches to a
costlier model via `MODEL_OVERRIDE`, or takes more tool steps. Nothing states
this limit, and no test pins it.

This matters directly to the plan of record's PR C, and it is the third
independent reason the rejection's "wire `cost_budget` +
`MANGOMAS_EVAL__MAX_MEAN_COST_USD`, both of which already exist" does not hold:
wrong harness (§1.2), and the wiring is blocked by a protocol return type that
discards the only data that would make it meaningful.

### N4 — a misspelled `json_field` path loads clean and can never accept · Medium-high

`json_field` is validated for structural correctness — `field` non-empty,
exactly one comparison — but the **path is never checked against anything**.
Probed:

```
ReviewResult schema props: ['feedback', 'passed', 'score', 'suggestions']
typo'd field loads OK -> pased
typo predicate on passing review -> False  (never accepts -> MaxStepsExceeded)
correct predicate               -> True
```

A `loop` over `reviewer` accepting on `field: "pased"` validates, compiles, and
returns `False` for **every** response. The loop exhausts `max_steps` and raises
`MaxStepsExceeded`.

The runtime behaviour is correct and deliberate — `predicate/_client.py`'s
`compile_predicate` docstring states the closure is total, so "a path that does
not resolve is simply 'not accepted', so non-convergence keeps surfacing as
`MaxStepsExceeded` rather than as a new error type." Nothing to change there.
The gap is that **a typo and a genuinely non-converging model produce the
identical symptom**, and one of them is free to detect at load time: the
addressed agent's schema is right there. `ReviewResult.model_json_schema()`
names its four properties, and `StructuredOutputAgent` already holds the model
(`agents/_structured.py:68`, as `self._schema`).

Note the interaction with §1.1: `json_field` is the *recommended* fix for
self-report matching, and adopting it as recommended moves a silent-acceptance
bug (`contains` accepting a rejecting review) to a silent-never-accepts bug
(a typo'd path). The recommendation is still right; it needs the load-time check
to be safe to follow.

This also replaces the plan of record's milestone B1 as written. That milestone
asks a `json_field` predicate to "reject output that `VALIDATE_OUTPUT` would
reject", but the predicate is compiled from pure data at graph-load time and has
no agent instance, no schema, and — under the `workflow`/`eval`/`rag`/`cognitive`
independence contract and the pure-domain rule — no business importing
`agents/`. Validating the **path** against a schema supplied by the caller
achieves the same intent, is implementable without a layering breach, and
additionally catches the typo. See the plan's PR C.

---

## 3. SDLC lenses

Where the repository stands per role, on executed evidence at `92d5e9d`.

| Lens | State | The one thing that matters next |
|---|---|---|
| **Requirements / architecture** | Strong. 33 specs, 34 ADRs, boundary honesty enforced (ADR-0033), INV-16 scope discipline holds under three council attempts to breach it | Nothing. Stop re-deciding settled scope |
| **Development** | Healthy. 2935 tests green, `mypy --strict` clean, import-linter contracts pass, god-file decomposition done | Load-time validation of `json_field` paths (N4) — the recommended fix for §1.1 is unsafe to adopt at scale without it |
| **QA / test** | Broad but structural. 23 coverage floors, nine at 100 %; no mutation score; no behavioural gate that can fail; the cost dimension measures characters, not spend (N3) | Plan-of-record PR A (`make guard-probe`), then PR C — but settle N3 first, or PR C ships a gate that cannot detect the regression it names |
| **Release / ops** | **Blocked and inconsistent.** Version claims 0.4.0, tag says v0.1.0, deploy has never executed | Cut v0.4.0 — spec-0024 is done, D8 set the cadence a month ago |
| **Security / supply chain** | **Weakest lens, and newly so.** Production's pinned closure is neither audited nor updated (N2); toolchain parity has a hole two parity tests miss (N1) | N2 first — it is the only finding here touching the artefact that reaches production |
| **Governance** | Mechanised in-repo, **unenforced at the forge** | D1. One settings change, open 28 days |

The asymmetry is the finding: development and architecture are in better shape
than the process around them. Three councils in seven days produced no code and
one net-new correct finding between them, while D1 — a single settings change
identified on 2026-08-22 — remains open and makes the headline differentiator
unfalsifiable regardless of how good the in-repo mechanism is.

The second pattern, visible only across all four new findings, is narrower and
more useful: **this repository's failures now cluster at boundaries where a
capability was built and the constraint that makes it correct was written as
prose instead.** `json_field` shipped without a guard on which predicate kinds
are legal (§1.1) or on whether the path resolves (N4); the cost dimension
shipped without stating that it measures characters (N3); the lockfile shipped
with a Dockerfile contract and no freshness or audit gate (N2); the mypy hook's
dependency list shipped with a comment claiming parity that two parity tests do
not check (N1). Every one is small. None is a design error. All four are the same
omission, and spec-0022 R15 already names it.

---

## 4. Decision register

Extends the register in
[`20260822-next-steps-roadmap-analysis.md`](20260822-next-steps-roadmap-analysis.md) §4;
D1–D9 there are unchanged. These cannot be taken by an agent.

| # | Decision | Recommendation |
|---|---|---|
| D10 | **`main` disposition.** 11 ahead / 263 behind, carrying a superseded lineage whose content exists on the default branch. Reconcile under an ADR supersession record, or delete with a recorded rationale. | Delete, with the rationale recorded in the D-register. The 11 commits are history, not content. Do it in the same sitting as D11 |
| D11 | **Rename the default branch** `feat/initial-release` → `main` after D10 clears the name. Cheap, and removes the single loudest enterprise-readiness signal in the repository. | Do it with D10 |
| D12 | **Release-number reconciliation.** `pyproject.toml` says 0.4.0; the newest tag is v0.1.0. Either tag v0.4.0 from the default branch (D8's cadence) or reset the version string to reflect what has actually shipped. | Tag v0.4.0. spec-0024 is delivered; deploy has never executed and a release publish is what exercises it |
| D13 | **`requirements.lock` ownership** (N2). Keep the file and gate it, or drop it and pin through a manifest Dependabot parses (`requirements.txt`). Keeping it unmanaged is the one option the evidence rules out. | Keep and gate — the Dockerfile contract and its two-directional tests are already built. Rename to `requirements.txt` only if Dependabot coverage is judged more valuable than the filename's signal |
| D14 | **Review-input protocol.** Adopt the rejection's fetch-receipt rule — URL, HTTP status, commit SHA — and discard any contribution lacking one. | Adopt. Three councils, seven days, one net-new finding is the cost case |
| D15 | **Cost semantics** (N3). Either (a) keep cost declared-in-dataset, document the limit, and pin it with a test — cheap, honest, and the cost gate then only ever claims to compare targets at equal declared budget; or (b) thread response metadata through the `Target` seam so cost is measured, which needs token usage from the adapters and an additive protocol change. | (a) now, (b) only if a cost gate is ever meant to **block** a merge. Do not ship (b)'s gate on (a)'s data |

---

## 5. What this changes about the plan of record

Less than the volume of this document suggests.
`docs/plans/20260916T214636Z-reliability-evidence-plan.md` remains correct and
correctly ordered. PR A (prove the gates can fail) → PR C (a behavioural gate)
is the right spine, and its reasoning about mutation-test scope is better than
the alternative proposed against it.

Four amendments, in the order they bite:

1. **Milestone B1 is not implementable as written**, which is the likeliest
   reason it did not land with B0. It asks the predicate to agree with
   `VALIDATE_OUTPUT`; the predicate is compiled from pure data at graph-load
   time with no agent, no schema, and no licence to import `agents/`. N4 gives
   the achievable form — validate the addressed **path** against a schema the
   caller supplies — which serves the same intent and also catches the typo.
2. **PR C needs N3 settled before it sets a cost threshold.** As built, a
   `mean_cost_usd` gate fires on verbosity and is blind to a model swap, a
   pricier token, or extra tool steps. Publishing a threshold over that number
   without stating what it measures would put a figure in
   `docs/testing/regression.md` that does not mean what its name says.
3. **Two supply-chain milestones belong ahead of PR A.** N1 and N2 are days of
   work, block nothing, and N2 is the only open finding touching the artefact
   that reaches production. N1 additionally has a deadline: it must land before
   the Dependabot queue is drained, or the four `pre_commit` PRs merge clean and
   the guard arrives red.
4. **A2's count is stale.** "Freeze all twenty-two floors" — there are 23, nine
   at 100 %.

Nothing here re-sequences PR A's internals, PR C's `pass^k` work, the
`llm_judge` demotion, or PRs D–F. Those stand.
