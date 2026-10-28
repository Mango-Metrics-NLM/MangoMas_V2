# Spec-0045: Plan-search spike (kill criterion)

- **Status:** Draft — a spike, not a feature. Produces a measurement and a
  decision; lands no runtime code.
- **Linked ADR:** _none — no boundary change_ (nothing under `src/` changes)
- **Linked CHANGELOG entry:** _none_ (an analysis document is not a release item)
- **Delivery plan:** `docs/plans/20261027T220000Z-civ6-method-port-plan.md` (PR 4)
- **Depends on:** spec-0042, spec-0043 merged; spec-0040 calibration applied

## Problem

The Civ6→MangoMas report recommends "bounded, side-effect-free plan
search" shipping "only if it beats best-of-N at equal cost". That is the
right gate and the wrong order: it schedules the build before the
measurement. Civ6-AlphaZero's own record of what MCTS cost to make
trustworthy — five duplicate backup loops, one-hot targets masquerading as
visit distributions, a tie-broken argmax oracle passing vacuously, four
unseeded RNG sources found only by diffing runs — is the strongest
evidence in either repository that tree search over LLM proposals is the
expensive case, and it has no exact terminal oracle to write the equivalent
forced-win test against. So the decision is made first, with the paired
statistic, and the feature spec is written only if search survives.

## Requirements

- Three candidates, each a thin script under
  `examples/spikes/plan_search/` (outside `[tool.coverage] source`; imports
  only public `mangomas` surfaces; registered as **no** target, scorer, or
  judge — nothing in a registry):
  1. **best-of-N** — N planner samples, reviewer picks (N = 4).
  2. **beam** — width 2, depth 2, reviewer score as the beam heuristic.
  3. **UCT** — planner proposals as children, reviewer score as the value
     signal, no rollouts, no learned value model, budget-bounded.
- **Equal measured budget**: total planner + reviewer tokens and tool-call
  count per task, read from the `cost_budget` basis — not iteration
  counts. Any candidate that exceeds the budget on a task forfeits that
  task (scored as a loss, recorded as `forfeit`).
- Only `planner` / `reviewer` / `chat` participate. `tool` never runs
  inside search (INV-16: cognition proposes, the harness disposes). The
  spike asserts this with the bypass-inventory pattern on its own
  directory.
- The reviewer is modelled as **cooperative** (its score is the node value,
  no sign flip, no min-node). This is a recorded choice; a min-node
  (worst-case-robust) variant is out of scope and the analysis doc must
  say which reading was taken.
- Comparison: `eval paired` (spec-0043) with `llm_pairwise`, grouped by
  `task_family`, on **two** families with ≥ 30 rows each; the same seeds
  and the same `AgentRequest`s to all three candidates; every run's inputs
  and outputs stored as `observed` decision records (spec-0044) so the
  result is reproducible from the records alone.
- **Pre-registered kill criterion** (this section is the register):
  search is promoted to a feature spec **only if** its score-rate lower
  bound (spec-0042, `confidence = 0.95`) beats best-of-N on **both**
  families at equal budget. Beating beam is irrelevant. Beating best-of-N
  on one family is a loss. A `position_inconsistency_rate` above
  `max_position_inconsistency` voids the run rather than the candidate.
- Output: `docs/analysis/<date>-plan-search-spike.md` with the paired
  summaries, the budget accounting per candidate, the forfeit counts, the
  reviewer-sign reading, and the verdict in one sentence. If the verdict
  is "no", this plan closes and **no `planning/` package is created**;
  re-opening needs a new spec that cites a changed premise (a value model,
  a different task family, a cheaper judge).

## Scenarios (WHEN/THEN)

A spike has no gated code, so these are acceptance checks on the
*measurement*, each written as a test in `examples/spikes/plan_search/tests/`
that is run by hand and whose invocation is recorded in the analysis doc:

- WHEN the three candidates run on the same row THEN the `AgentRequest`
  content sent to the planner is identical across all three (digest
  equality from the decision records) AND WHEN a candidate mutates the
  request THEN the digest check fails.
- WHEN a candidate exceeds the budget on a row THEN that row is a forfeit
  for it AND WHEN it stays within budget THEN it is judged.
- WHEN UCT's value signal is replaced by a constant (mutation) THEN its
  score-rate against best-of-N is indistinguishable from random pairing
  (interval contains 0.5) — the proof that the value signal did anything
  at all before any claim about it is believed.
- WHEN the two families are the same family relabelled THEN the group
  bootstrap collapses to a row bootstrap and the test flags it (the
  families must be different for "both families" to mean anything).
- WHEN `tool` is dispatched inside any candidate THEN the spike's bypass
  inventory fails.

## Config / env additions

_None in `Settings`._ Spike parameters are module constants in
`examples/spikes/plan_search/config.py` (`N_BEST_OF = 4`, `BEAM_WIDTH =
2`, `BEAM_DEPTH = 2`, `UCT_C = 1.4`, `BUDGET_TOKENS_PER_TASK`,
`BUDGET_TOOL_CALLS_PER_TASK = 0`, `MIN_ROWS_PER_FAMILY = 30`,
`CONFIDENCE = 0.95`) — no literals in the scripts.

## Protocol / contract impact

- New/changed protocols: _none_.
- New error types: _none_.
- Registry additions: _none_ — the candidates are not targets, so they
  cannot leak into `mangomas eval` by name.

## Backwards-compatibility

- Nothing under `src/` changes; `make gate` is unaffected by this PR
  except for the new analysis document passing `frontmatter-lint` if it
  carries frontmatter.

## Test plan

- Hand-run checks in `examples/spikes/plan_search/tests/` (listed above),
  invoked as `python -m pytest examples/spikes/plan_search/tests --no-cov`
  and the exact command + output hash recorded in the analysis doc.
- Gates: none in CI. The gate is the kill criterion, and it is enforced by
  the plan's Deferred section: a `planning/` package without a citing
  analysis doc is scope creep by definition.

## Acceptance criteria

- [ ] Three candidates run at equal measured budget on two families with
  the same seeds and requests; decision records exist for every row.
- [ ] Constant-value mutation on UCT recorded as indistinguishable from
  random (proves the comparison can see a difference).
- [ ] `docs/analysis/<date>-plan-search-spike.md` states the reviewer-sign
  reading, the budget accounting, the forfeits, and a one-sentence verdict.
- [ ] Verdict "yes" → a new feature spec is opened citing this document;
  verdict "no" → the delivery plan's status becomes Done with no
  `planning/` package.
