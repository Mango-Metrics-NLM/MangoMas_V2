# Spec-0055: Loop evidence ledger and the false-accept rate

- **Status:** Draft
- **Linked ADR:** [ADR-0042](../docs/adr/0042-governed-self-improvement-loop.md) (Proposed)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Governed self-improvement loop: decision records (ADR-0042 / spec-0052..0055)`
- **Delivery plan:** [milestone M4](../docs/plans/20261123T000000Z-governed-self-improvement-loop-plan.md)

## Problem

The proposal's measure of record, the false-accept rate, has no definition,
denominator, data source or sample size. And in this setup the agent acts
under the maintainer's GitHub identity, so any record it can write, whether a
label, a comment or a status file, is not evidence about it.

## Requirements

- **R1 Derived, not written.** `scripts/build_loop_ledger.py` rebuilds the
  ledger from GitHub (pull requests, check-run conclusions, merges, closures,
  reverts and follow-up fixes) and CI artifacts. It ingests every PR whose head
  starts with `claude/`, classifies it, and flags any head that matches neither
  governed prefix. Output: append-only JSONL, one event per line, each with a
  `source` field. Rebuilding and comparing detects tampering with any stored
  copy.
- **R2 Events.** Derived: `item_approved`, `proposal_reviewed`, `pr_opened`,
  `gate_result`, `scope_result`, `verdict`, `merged`, `closed`,
  `escaped_defect`. Self-reported: `blocked`, the implementer's typed refusal,
  recorded with `source: self_reported`. It stops an item and never counts
  toward acceptance.
- **R3 Verdict channel.** Audit verdicts (`accept`, `false_accept`,
  `false_reject`, `scope_violation` or `unsure`, with a reason and the audit
  minutes) come only through a channel implementer sessions cannot write.
  Candidates: commits signed with a maintainer key that sessions do not hold;
  a store written with a credential sessions do not hold. The choice is
  recorded here before M4 lands; a verdict from any other channel is rejected.
- **R4 Definitions.**
  - *Gate-passed PR:* every required check green, including the scope check.
  - *False accept:* a gate-passed loop PR the auditor would reject, or an
    escaped defect (R5).
  - *False reject:* a loop PR the gate failed that the auditor judges correct,
    estimated on a sample.
  - *Planted-defect escape:* a guard-probe mutation, or from M6 a diff-scoped
    mutant, that the gate fails to catch.
  - *BLOCKED rate:* items the implementer declined with a typed reason.
- **R5 Escaped defect.** A merged loop PR whose merge is reverted, or that a
  later commit fixes and cites, within 14 days of merge.
- **R6 Bounds.** Every rate is reported with its counts and an exact one-sided
  95% (Clopper–Pearson) upper bound. The false-accept bound is computed only
  on a cohort whose membership is fixed before outcomes are known: every
  gate-passed PR in Shadow (a census) and, from L0, a random cohort drawn when
  each PR opens (for example, a hash of the PR number below a fixed
  threshold). Numerator: cohort PRs with a `false_accept` verdict or an
  escaped defect. Denominator: audited cohort PRs. Targeted audits of
  security-relevant or reverted PRs outside the cohort are reported
  separately and never pooled into the bound, because their inclusion depends
  on the outcome being measured; a false accept they find still triggers the
  halt rule.
- **R7 Current-process baseline.** The escaped-defect rate, by the same rules,
  of PRs merged into the trunk over the previous six months (`dev`, and before
  the November rename `feat/initial-release`) whose head is not a governed
  prefix. It includes interactive Claude Code sessions, because identity cannot
  separate them from human work here, so it is named for what it measures: the
  current process, not human-only work (ADR-0033).
- **R8 Report.** `make loop-metrics` prints, per stage: audited count, false
  accepts and their bound, the false-reject estimate, the escaped-defect rate
  against the baseline, the BLOCKED rate, lead time, and cost and audit
  minutes per accepted PR. It informs the maintainer's stage decision and
  never promotes on its own.
- **R9 No secrets.** The ledger holds structured metadata only: event type
  and source, PR numbers and item IDs, SHAs, check names and conclusions,
  timestamps and durations, verdict codes with a sanitised reason, audit
  minutes, and cost figures. It never holds tokens, credentials, environment
  values or raw logs.

## How many audits are enough

Exact one-sided 95% upper bounds on the false-accept rate:

| Audited PRs | 0 false accepts | 1 | 2 |
|---|---|---|---|
| 30 | 9.5% | 14.9% | 19.5% |
| 60 | 4.9% | 7.7% | 10.1% |
| 100 | 3.0% | 4.7% | 6.2% |
| 300 | 1.0% | 1.6% | 2.1% |

## Scenarios (WHEN/THEN)

- WHEN the ledger is rebuilt from the recorded fixture THEN it equals the
  expected events exactly; WHEN a stored copy has been edited THEN the
  comparison reports the difference.
- WHEN a verdict arrives through an unauthorised channel THEN it is rejected
  and reported; WHEN it arrives through the recorded channel THEN it is
  accepted.
- WHEN a merged loop PR is reverted on day 10 THEN it counts as an escaped
  defect; WHEN it is reverted on day 20 THEN it does not.
- WHEN 30 audits show zero false accepts THEN the report shows a bound of 9.5%,
  not 0%.
- WHEN a targeted audit outside the random cohort finds a false accept THEN it
  is reported separately and triggers the halt rule, and the cohort bound is
  unchanged.
- WHEN a stage has fewer audits than its exit criterion THEN the report says
  "insufficient evidence" instead of a rate.

## Config / env additions

None in `Settings`.

## Protocol / contract impact

- New/changed protocols: none in `src/`.
- New error types: none.
- Registry additions: none.

## Backwards-compatibility

- Additive: a new script, a make target and a data file format.

## Test plan

- Unit: `tests/test_build_loop_ledger.py` over recorded GitHub API fixtures (no
  network in CI); a property test for the bound function against a reference
  implementation; both registered in `SCRIPTS_TESTS`.
- Gates: a `MUT-LEDGER-01` guard-probe entry; a builder that accepts an
  unauthorised verdict must turn it red.

## Acceptance criteria

- [ ] Every scenario is a test, in both directions.
- [ ] The verdict channel is chosen and recorded in this spec.
- [ ] The current-process baseline is computed and committed with its date
      range.
- [ ] CHANGELOG updated.
