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
- **Relationship to prior work:** this document does **not** supersede
  [`20260916-council-roadmap-adjudication.md`](20260916-council-roadmap-adjudication.md)
  or [`20260916T214636Z-reliability-evidence-plan.md`](../plans/20260916T214636Z-reliability-evidence-plan.md).
  It confirms that the plan of record already covers most of what was proposed
  as new, and adds the two findings nothing in the tree covers.

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

These two are not in the council document, not in the rejection, and not in any
in-tree analysis or plan. Both are in the repo's own signature defect class — a
constraint asserted in prose while the mechanism does something narrower — and
in the second case a **passing test** asserts the effect that is missing.

### N1 — the mypy hook's `additional_dependencies` has no parity guard · High

`tests/tooling/test_toolchain_pin_parity.py` was written for exactly this
failure mode, and its `TOOL_PINS` tuple covers two things: `ruff` and `mypy`,
rev-versus-pin. `grep -rn additional_dependencies tests/` returns **nothing**.

So the eight entries under the mypy hook
(`.pre-commit-config.yaml:29-36`: `fastapi>=0.115`, `pydantic>=2.7`,
`pydantic-settings>=2.3`, `httpx>=0.27`, `opentelemetry-api>=1.25`,
`opentelemetry-sdk>=1.25`, `typer>=0.12`, `starlette>=0.40`) are free to drift
from `pyproject.toml`'s runtime ranges, and **four open Dependabot PRs each
widen the gap**: merging `dependabot/pre_commit/starlette-1.6.0` makes local
pre-commit typecheck `src/` against starlette 1.6 while the application resolves
starlette transitively through `fastapi>=0.115` (which caps it well below 1.0).
The hook's own comment claims the list "covers exactly the runtime deps" —
that is the assertion with no mechanism behind it.

The consequence is the one the parity test's docstring already describes, one
level down: a contributor's pre-commit and CI disagree, and it surfaces as a
type error nobody else can reproduce.

### N2 — `requirements.lock` is unmanaged and unaudited, and a passing test implies otherwise · High

The lockfile is what the **production image installs**: `Dockerfile:45-47`
copies it and passes `-c /tmp/requirements.lock` to `pip install`. It holds 32
`==` pins of the transitive runtime closure and is generated by `pip-compile`.

Three gaps:

1. **Not audited.** `grep -n requirements.lock .github/workflows/*.yml Makefile`
   returns nothing. The `pip-audit` job (`ci.yml:192-212`) runs `make pip-audit`
   over the `make install` environment — resolved from pyproject's `>=` ranges,
   **not** the lock's pins. The versions production actually ships are never
   scanned.
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

---

## 3. SDLC lenses

Where the repository stands per role, on executed evidence at `92d5e9d`.

| Lens | State | The one thing that matters next |
|---|---|---|
| **Requirements / architecture** | Strong. 33 specs, 34 ADRs, boundary honesty enforced (ADR-0033), INV-16 scope discipline holds under three council attempts to breach it | Nothing. Stop re-deciding settled scope |
| **Development** | Healthy. 2935 tests green, `mypy --strict` clean, import-linter contracts pass, god-file decomposition done | PR B1 — make `json_field` and `VALIDATE_OUTPUT` provably agree |
| **QA / test** | Broad but structural. 23 coverage floors, nine at 100 %; no mutation score; no behavioural gate that can fail | Plan-of-record PR A (`make guard-probe`), then PR C |
| **Release / ops** | **Blocked and inconsistent.** Version claims 0.4.0, tag says v0.1.0, deploy has never executed | Cut v0.4.0 — spec-0024 is done, D8 set the cadence a month ago |
| **Security / supply chain** | **Weakest lens, and newly so.** Production's pinned closure is neither audited nor updated (N2); toolchain parity has a hole (N1) | N2 first — it is the only finding here touching what ships |
| **Governance** | Mechanised in-repo, **unenforced at the forge** | D1. One settings change, open 28 days |

The asymmetry is the finding: development and architecture are in better shape
than the process around them. Three councils in seven days produced no code and
one net-new correct finding between them, while D1 — a single settings change
identified on 2026-08-22 — remains open and makes the headline differentiator
unfalsifiable regardless of how good the in-repo mechanism is.

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

---

## 5. What this changes about the plan of record

Very little, which is the point.
`docs/plans/20260916T214636Z-reliability-evidence-plan.md` remains correct and
correctly ordered. PR A (prove the gates can fail) → PR C (a behavioural gate)
is the right spine, and its reasoning about mutation-test scope is better than
the alternative proposed against it.

Three amendments:

1. **PR B is not done.** B0 landed; B1 did not. Add the loader guard from §1.1
   to it — the capability shipped without the constraint that makes it the
   supported path.
2. **Two new milestones ahead of PR A**, in the new plan: N1 and N2 are days of
   work, block nothing, and N2 is the only open finding that touches production
   artefacts.
3. **A2's count is stale.** "Freeze all twenty-two floors" — there are 23, nine
   at 100 %.
