# Reliability evidence — delivery plan

- **Branch:** `claude/mangomas-v2-roadmap-analysis-zny6rc`
- **Date:** 2026-09-16
- **Target release:** rolling (no version cut in this program)
- **Status:** Draft
- **Specs:** spec-0032 (PR B, written) · spec-0033 (PR A) · spec-0034–0036 (PRs C–E, not yet written)
- **ADRs:** ADR-0031 (acceptance predicates), ADR-0032 (tool effect classes) — the
  other PRs change no boundary

## Executive summary

Six PRs, dependency-ordered, that move this repository's strongest constraints
out of prose and into mechanism. The sequencing constraint is a single fact
established in
[the council adjudication](../analysis/20260916-council-roadmap-adjudication.md):
**the repo already knows what to do and has not wired it up.** `specs/TEMPLATE.md`
mandates two-sided gate proofs, `mango-mutation-proof` catalogues four
silent-pass failures this repo has already hit, and `eval-gate.yml` contains a
working behavioural-gate skeleton — none of which can fail a build today. So
PR A mechanises falsifiability first, because until a gate can be shown to fire
there is no point adding gates; PR B closes a real correctness hole that would
otherwise corrupt everything PR C measures; PR C makes behaviour gateable; PRs
D–F extend outward. PR F is parallel-safe and can land at any time.

Rough sizing: A ≈ 2 weeks, B ≈ 1 week, C ≈ 4–6 weeks, D ≈ 3 weeks,
E ≈ 2–3 weeks, F ≈ 1 day. Estimates, not commitments.

**The acceptance test for the whole program:** a pull request that makes the
platform worse at routing, planning, reviewing or retrieving must fail CI even
though it typechecks, passes every coverage floor and satisfies every existing
gate.

---

## PR A — Prove the gates can fail (spec-0033)

The repo's twelve-target `make gate` chain measures structure. Nothing
establishes that any of it rejects a defect. This PR mechanises the procedure
`mango-mutation-proof` already documents.

### Milestone A0 — `make guard-probe`

- **Failing test first:** `tests/harness/test_guard_probe.py::test_probe_detects_a_disarmed_gate`
  — a probe manifest whose recorded mutation no longer makes its gate fail must
  itself fail the probe run. Written before the target exists, so the probe is
  proven two-sided from the first commit.
- **Depends on:** nothing — parallel-safe.
- Add `scripts/guard_probe.py` and a `make guard-probe` target. For each
  load-bearing gate, a manifest entry records: the gate command, a discriminating
  mutation (patch or in-place edit), and the expected failure. The script applies
  the mutation, asserts the gate goes red, restores, and asserts it goes green.
- Seed the manifest with the gates whose silent-pass modes are already recorded
  in `.claude/skills/mango-mutation-proof/SKILL.md`: `check_coverage.py`'s glob
  recursion, `check_protected_paths.py`'s base-ref read, the import-linter
  contracts, and the frontmatter linter's `MIN_AGENT_FILES` floor.
- Wire as a CI job alongside `protected-paths`. **Do not** add it to `make gate`
  yet — it mutates the tree, and `gate` must stay safe to run on a dirty
  checkout.
- Delete or rewrite any gate whose mutation cannot be made to fail it. That
  outcome is a success of this milestone, not a failure of it.

### Milestone A1 — mutation score on the three surfaces that matter

- **Failing test first:** `tests/test_mutation_baseline.py::test_baseline_is_a_lower_only_ratchet`
  — lowering a recorded baseline without a paired decision record fails.
- **Depends on:** A0 (the probe proves the ratchet can fire).
- Add `mutmut` as a dev extra with `[tool.mutmut]` in `pyproject.toml`:
  `source_paths`, `pytest_add_cli_args_test_selection`, and
  `mutate_only_covered_lines = true` to keep runtime tractable.
- Scope to `src/mangomas/core`, `src/mangomas/workflow`,
  `src/mangomas/composition` — the three surfaces where a defect is most
  expensive and line coverage is least informative.
- Record the measured score per surface as a **lower-only ratchet**, mirroring
  the existing `SCRIPTS_FLOOR` convention in `Makefile` (measured value, floor
  set below it with a stated margin, comment explaining the gap).
- ⚠️ **`pyproject.toml` is a protected path** (ADR-0030). This milestone's commit
  needs `BREAKING-CHANGE: pyproject.toml — add [tool.mutmut] mutation config`.

### Milestone A2 — freeze the coverage floors

- **Failing test first:** `tests/test_check_coverage.py::test_floor_increase_requires_a_decision_record`.
- **Depends on:** A1 (the mutation score is what replaces floor-raising as the
  quality signal).
- Freeze all twenty-two floors in `scripts/check_coverage.py` at current values.
  Eight already sit at 100 %; raising the rest buys test volume, not
  discrimination.
- Add a guard requiring any floor increase to cite a spec or ADR, so the
  ratchet grows by decision rather than by reflex.

---

## PR B — Acceptance you can trust (spec-0032, ADR-0031) — **landed first**

`workflow/predicate.py` offers `contains` and `regex` over `response.content`.
`ReviewerAgent` emits `{"passed": bool, "score": float, …}`. A loop that
iterates until review passes must therefore text-match the reviewer's own
serialised self-report — and **measurement showed it fails in both
directions**: the natural needle `"passed": true` never matches compact
`model_dump_json()` output (false negative, loop raises `MaxStepsExceeded` on
an approved review), and the quoteless needle that fixes it then matches prose
inside `feedback`/`suggestions` (false positive). Fixing one manufactures the
other. See §3 of the adjudication, revised after probing. Spec-0032 / ADR-0031.

### Milestone B0 — a `json_field` predicate kind

- **Failing test first:** four regression tests in
  `tests/test_workflow_predicate.py`, each observed failing against `contains`
  before `json_field` existed — two false negatives (compact and pretty-printed
  serialisation of an *approving* `ReviewResult`) and two false positives (a
  *rejecting* `ReviewResult` whose `feedback` / `suggestions` carry the
  quoteless needle). Together they are the two-sided proof.
- **Depends on:** nothing, in the event. The plan sequenced A first so the
  probe harness would make the two-sided proof mechanical; that dependency was
  soft, and the plan's own Notes said B0's check comes first. The four
  regression tests were written by hand instead, and the measurement they
  produced corrected §3 of the adjudication — which is why B landed ahead of A.
- Extend `PredicateSpec.kind` to `Literal["contains", "regex", "json_field"]`
  with optional `field`, `equals`, `at_least` and `at_most`. The model is
  `frozen=True, extra="forbid"`, so optional additions leave every existing
  graph valid — additive and default-safe.
- Compile via `mangomas.core.structured.parse_llm_json_object`, which is already
  the repo's permanent JSON-recovery facade and is synchronous, so
  `AcceptanceFn` stays sync as `core/loop.py` requires. This is an *import* of a
  protected path, not a change to one — no trailer needed.
- Raise `ConfigError` at compile time (not per call) when `field` is absent for
  `json_field`, when both `equals` and `at_least` are set, or when the response
  is unparseable — matching the existing flag-validation behaviour.
- Document in `docs/workflow/graphs.md` and the `mango-workflow` skill that
  `contains` over a structured agent's output is a self-report match, and that
  `json_field` is the supported way to bind acceptance to a parsed field.

### Milestone B1 — make the two guards agree

- **Failing test first:** `tests/workflow/test_predicate.py::test_json_field_rejects_output_that_validate_output_would_reject`.
- **Depends on:** B0.
- A `json_field` predicate on a structured agent must reject output that
  `MANGOMAS_AGENTS__<NAME>__VALIDATE_OUTPUT` would reject. Today the predicate
  never consults the parsed model, so the two guards cannot agree by
  construction.

---

## PR C — A behavioural gate that can fail (spec-0034)

`.github/workflows/eval-gate.yml` already drives a live Mango-Mas API through
the sibling harness, with the bridge and converter unit-covered at a 100 %
floor. It is `workflow_dispatch`-only, `EVAL_GATE_ENABLED`-gated, five rows,
`exact_match`, `pass_rate >= 0.5`, one agent. This PR promotes it.

### Milestone C0 — three operating profiles as the CI matrix

- **Failing test first:** `tests/deploy/test_profile_contract.py::test_every_profile_env_set_validates_against_settings`
  — mirrors the existing `test_env_example_contract.py` both-directions pattern.
- **Depends on:** nothing — parallel-safe with A and B.
- Define **Local Solo**, **Team Evaluation** and **Governed Service** as named
  env sets in `deploy/profiles/`, each a complete `MANGOMAS_*` assignment.
  GLM-5.3's argument stands: benchmarking one config, demoing another and
  documenting a third is how a platform loses its own thread.
- Profiles are the matrix axis for everything below, not a separate phase.

### Milestone C1 — `pass^k` as the reliability metric of record

- **Failing test first:** `tests/eval/test_runner_trials.py::test_pass_pow_k_falls_when_one_trial_of_k_fails`.
- **Depends on:** C0.
- `pass^k` is a *runner* concern, not a scorer: it needs k trials of the same
  row. Add `MANGOMAS_EVAL__TRIALS` (default `1`, preserving today's behaviour
  exactly) and an additive `pass_pow_k` field on `EvalReport` = the fraction of
  rows where **all** k trials passed.
- Add `MANGOMAS_EVAL__MIN_PASS_POW_K` to the existing threshold gate in
  `eval/gate.py`, composed through `merge_gate_results` like every other
  verdict. Default unset — off unless configured.
- The metric earns its place: at k=8, a 90 %-pass@1 agent reports 57 %.
  Single-trial `pass_rate` cannot see that.

### Milestone C2 — demote `llm_judge` until it is calibrated

- **Failing test first:** `tests/eval/test_gate.py::test_uncalibrated_judge_scores_are_reported_but_not_gated`.
- **Depends on:** C1.
- `llm_judge` scores through `context.llm` — the same client as the system under
  test — so self-preference bias is structural. Today it can contribute to a
  gate verdict with no calibration evidence at all.
- Add `MANGOMAS_EVAL__JUDGE_CALIBRATION_PATH`: a record carrying sample size,
  Cohen's κ, Spearman ρ, the judge model id and the measurement date. Absent or
  stale → judge scores are **reported but excluded from the gate verdict**,
  exactly as `cost_budget` behaves today.
- Calibration target: 200–500 rows with expert pass/fail labels, iterating the
  rubric to κ > 0.8 or ρ > 0.85. Rotate candidate positions and decompose the
  rubric rather than asking for one holistic score.
- Record "self-preference not measured" distinctly from "measured as unbiased".
  They are different claims and the gate must not conflate them.

### Milestone C3 — grow the dataset and promote the gate

- **Failing test first:** a deliberately-regressed agent prompt, committed to a
  fixture branch, must fail the promoted gate. This is the program's headline
  acceptance test.
- **Depends on:** C1, C2, and B0 (a loop's acceptance must be trustworthy before
  its trajectory is worth asserting on).
- Grow `eval_harness_bridge/datasets/` past five rows, with per-agent coverage
  for `chat`, `summarize`, `tool`, `planner` and `reviewer` — not `chat` alone.
- Raise `pass_rate >= 0.5` to a measured floor with a stated margin, following
  the `SCRIPTS_FLOOR` convention rather than a round number.
- Promote from `workflow_dispatch` to `pull_request`, using a recorded-cassette
  or stubbed-LLM path so vanilla runners can execute it. Keep the live-endpoint
  path as the nightly `workflow_dispatch` job it is today.
- Keep the `AGENTS_HARNESS_REF` fail-closed pin exactly as it is. It is already
  correct and is the kind of supply-chain discipline this program should not
  regress.

### Milestone C4 — does the router beat `chat`?

- **Failing test first:** `tests/eval/test_router_target.py::test_router_eval_fails_when_routing_is_randomised`.
- **Depends on:** C3.
- 27 agents are gated only by a frontmatter linter. Add a `router` eval target
  and a dataset of task descriptions → expected router, so the four routers'
  delegation is a measured claim rather than an assumed one.
- Add **Correction Yield** (fraction of reviewer interventions that changed the
  final answer for the better) and **Redundancy Ratio** (repeated reasoning
  paths per run) as report fields, adapting GEMMAS's IDS/UPR into engineering
  terms. These answer "is planner→reviewer worth its tokens" — a question no
  current scorer can ask.
- If a topology loses to a simpler baseline, **publish that**. A negative result
  is the credibility asset this repository most lacks, and it costs one
  benchmark run.

---

## PR D — Effect identities before write-capable tools (spec-0035, ADR-0032)

`composition/rag.py` builds a one-entry `ToolRegistry` holding the read-only
`RetrievalTool`. That is the cheapest moment this project will ever have to
establish an effect-identity contract, and it closes the week a second tool
lands.

### Milestone D0 — declare the effect class

- **Failing test first:** `tests/test_tools.py::test_every_registered_tool_declares_an_effect_class`.
- **Depends on:** A0.
- Add an additive `effect: Literal["read", "write"] = "read"` to `ToolSpec`, and
  an optional `effect_key(...)` returning a stable idempotency key for `write`
  tools. Defaults preserve today's behaviour byte-for-byte.
- `RetrievalTool` declares `effect="read"` explicitly rather than inheriting the
  default, so the declaration is a statement and not an omission.
- ⚠️ **`src/mangomas/core/tools.py` is a protected path.** Needs
  `BREAKING-CHANGE: src/mangomas/core/tools.py — additive tool effect class`.
- Ordering matters and is counterintuitive: idempotency precedes checkpointing.
  Checkpointing a `loop` or `fan_out` without effect identities produces
  duplicated side effects on replay. Establish the contract while the only tool
  is read-only; leave checkpoints themselves out of scope until a `write` tool
  actually exists.

---

## PR E — A stated trust boundary for untrusted content (spec-0036)

`.mcp.json` ships `fetch` and optional `github`; both return third-party content
into agent context. `mangomas rag ingest <path>` is the same channel. Neither
has a stated trust model.

### Milestone E0 — mark retrieved content as data

- **Failing test first:** `tests/rag/test_retrieval.py::test_retrieved_content_is_delimited_and_provenanced`.
- **Depends on:** nothing — parallel-safe with C and D.
- `RetrievalTool` output carries explicit provenance (source, chunk id) and is
  delimited as data rather than interpolated as prose into the prompt.
- `docs/security/trust-boundary.md` states plainly which channels are untrusted
  and what the system does about it.

### Milestone E1 — `.mcp.json` audit

- **Failing test first:** `tests/deploy/test_mcp_pins.py::test_every_server_is_version_pinned`.
- **Depends on:** E0.
- The six servers are pinned at `@2026.7.10` / `@2026.7.4` / `v0.20.1`, all
  predating the MCP `2026-07-28` revision. With a twelve-month minimum
  deprecation window, migration is a 2027 calendar item — record it as a dated
  follow-up rather than doing it now.
- Pin the audit itself as a test so a future unpinned server cannot slip in.
- ⚠️ **`.mcp.json` is a protected path** (ADR-0030). Any change needs
  `BREAKING-CHANGE: .mcp.json — <rationale>`.

---

## PR F — `AGENTS.md` bridge (parallel-safe; land any time)

- **Failing test first:** `tests/test_agents_md_contract.py::test_claude_md_first_line_imports_agents_md`.
- **Depends on:** nothing.
- Move build/test/lint/convention content from `CLAUDE.md` into a root
  `AGENTS.md`; keep Claude-specific surfaces (skills, hooks, agent corpus,
  `@imports`) in `CLAUDE.md` with `@AGENTS.md` as its first line.
- `AGENTS.md` is stewarded by the Agentic AI Foundation under the Linux
  Foundation and read by 30+ agents — Codex, Copilot, Cursor, Gemini CLI, Jules,
  Aider, Zed, Windsurf, Devin. Claude Code is the exception that still reads
  `CLAUDE.md`, which is exactly what the first-line import bridges.
- Highest value-per-effort item in this plan: roughly an afternoon, and it makes
  a 27-agent / 17-skill corpus legible to every other coding agent.

---

## Deferred / out of scope

- **A 28th agent, or any new workflow node kind.** The corpus has no routing
  eval; adding to it before C4 measures it increases maintenance without
  evidence. Re-opening requires C4 data showing the existing roster is
  saturated.
- **Coverage-floor increases.** Frozen by A2. Re-open only with a spec or ADR.
- **`gen_ai.*` dashboards or storage schemas.** The conventions left core
  semconv in v1.42.0 for a dedicated repository that has **no releases or tags
  yet**, so there is nothing to pin. `GENAI_SEMCONV_STATUS = "development-2026-09"`
  in `cognitive/constants.py` is already the correct hedge — the action is "no
  action". Revisit when a tagged release exists.
- **Checkpointing and workflow resume.** Blocked on PR D by design: checkpoints
  without effect identities duplicate side effects on replay. Not blocked on
  anything else.
- **Cognitive signal on a control path.** Stays a shadow channel until it
  publishes above-baseline agreement. ADR-0029's INV-16 (cognition proposes,
  the harness disposes) is unchanged by this plan.
- **A2A adapter.** Nothing here needs it; revisit after C4.
- **MCP `2026-07-28` migration.** Dated 2027 follow-up per E1; the deprecation
  window does not force it sooner.

## Verification

```bash
make gate                       # the full pre-PR chain, in CI's order
make guard-probe                # PR A onward — mutates the tree; clean checkout only
make protected-paths BASE_REF=origin/feat/initial-release

RUN_RAG=1 make rag              # PR B, E
RUN_INTEGRATION=1 make integration
```

Note the trunk: this repo's base branch is `feat/initial-release`, not `main`,
and `scripts/check_protected_paths.py` reads its policy from the **base ref**,
so a branch cannot shrink the set it is judged by.
