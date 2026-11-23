# Governed self-improvement loop — delivery plan

- **Branch:** `claude/modest-heisenberg-6gengs` (M0); each later milestone on its own branch
- **Date:** 2026-11-23
- **Target release:** rolling
- **Status:** Draft — M0 in review; nothing after M0 starts until ADR-0042 is accepted
- **Specs:** spec-0052 (M1), spec-0053 (M2), spec-0054 (M3), spec-0055 (M4); M5–M8 get specs when they start
- **ADRs:** ADR-0042 (Proposed)

## Executive summary

M0 records the decision. M1 makes the gate trustworthy for agent branches
before any agent branch exists: one scope check anchored on the default
branch, two branch classes, fail-closed. M2 and M3 give the loop its input: a
machine-readable backlog and the propose stage that fills it with approved
items. M4 makes the loop measurable: a ledger derived from GitHub and CI, a
human baseline and the false-accept bound. Only then does M5 start the Shadow
driver; M6 opens L1 with diff-scoped mutation; M7 adds held-out and live
behavioural checks; M8 runs L2 as an experiment. The constraint that shaped
the order: **nothing the loop does is trusted until something it cannot
influence has checked it**, so verification, input and measurement land
before autonomy.

Analysis and rationale:
[deep dive](../analysis/20261123-self-improvement-loop-deep-dive.md).

## PR A — Decision records (M0)

### Milestone M0 — analysis, plan, ADR-0042, specs 0052–0055

- **Failing test first:** none — docs only. `tests/tooling/test_decision_record_numbering.py`
  and `tests/tooling/test_doc_links.py` must stay green.
- **Depends on:** nothing.
- Lands the deep dive, this plan, ADR-0042 (Proposed), four Draft specs, index
  rows in `specs/README.md`, a `NEXT_STEPS.md` overlay and a CHANGELOG entry.

## PR B — Trusted base and scope check (spec-0052)

Owners: `mango-harness-dev` (policy, checker, sandbox fix) and `mango-ci-dev`
(workflow, Makefile). Touches `pyproject.toml`, a protected path, so a commit
carries `BREAKING-CHANGE: pyproject.toml — add the loop trusted-base policy (ADR-0042)`.

### Milestone M1a — policy and checker

- **Failing test first:** `tests/test_check_loop_scope.py` — an implementation
  branch whose diff touches `Makefile` fails; a proposal branch touching `src/`
  fails; an ungoverned branch passes; a missing base policy fails closed. Red
  until `scripts/check_loop_scope.py` exists.
- **Depends on:** ADR-0042 accepted.
- `[tool.mangomas.governance.loop]` in `pyproject.toml`;
  `scripts/check_loop_scope.py` (stdlib only, reusing `scripts/_governance.py`);
  `make loop-scope`.

### Milestone M1b — anchored workflow and required checks

- **Failing test first:** `tests/deploy/test_workflow_hardening.py`
  (`_EXPECTED_WORKFLOW_COUNT` 6 → 7) plus a new assertion that
  `loop-scope.yml` uses `pull_request_target`, never checks out a PR ref, and
  grants only `contents: read` and `pull-requests: read`.
- **Depends on:** M1a.
- `.github/workflows/loop-scope.yml`. `CONTRIBUTING.md`'s required-check list
  gains `Guard falsifiability probe` and `Loop scope check`; an admin marks
  both required on `dev`, `qa` and `main`. Record whether the implementer's
  push credential can write `.github/workflows/**` (spec-0052 R7).

### Milestone M1c — mutation proof and the staging hole

- **Failing test first:** a staged `conftest.py` is refused by the repair
  sandbox (red today); `MUT-LOOP-*` guard-probe entries turn the scope tests
  red when a trusted-base glob is removed or fail-closed is flipped.
- **Depends on:** M1a.
- Extend the startup-hook refusal in `src/mangomas/harness/isolation/base.py`
  to test-runner hook and configuration files; add the guard-probe entries.

## PR C — Machine-readable backlog (spec-0053)

### Milestone M2 — schema, validator, seed items

- **Failing test first:** `tests/test_check_backlog.py` — an item whose
  `allowed_paths` intersects the trusted base, a dependency cycle, or a
  contract-hash mismatch fails.
- **Depends on:** M1a (the validator reads the same policy).
- `backlog/<ID>.toml` items; `scripts/check_backlog.py`; `make backlog-check`
  wired into `gate`; three to five Shadow seed items written by hand.

## PR D — Propose stage (spec-0054)

### Milestone M3a — proposal format, assembler and citation checker

- **Failing test first:** `tests/test_check_proposal_evidence.py` — a citation
  whose quote does not match the file at the pinned SHA fails; a requirement
  with no acceptance criterion fails.
- **Depends on:** M1 (proposal branch class), M2 (item format).

### Milestone M3b — the saved workflow

- **Failing test first:** a corpus contract that the analyst, critic and
  security roles name only read-only agents.
- **Depends on:** M3a.
- `.claude/workflows/architecture-proposal.*`; first run on a maintainer
  request.

### Milestone M3c — ADR replay evaluation

- **Failing test first:** the replay harness refuses a snapshot that contains
  `.git` or files from after the decision.
- **Depends on:** M3b.
- Replay set curated by the maintainer from ADRs dated after the model's
  training cutoff, with a held-out subset.

## PR E — Evidence ledger and metrics (spec-0055)

### Milestone M4 — derived ledger, verdict channel, baseline

- **Failing test first:** rebuilding the ledger from a recorded GitHub fixture
  reproduces the expected events exactly; a verdict from an unauthorised
  channel is rejected.
- **Depends on:** M1, M2.
- `scripts/build_loop_ledger.py`; `make loop-metrics`; the human
  escaped-defect baseline over six months of `dev` history.

## PR F — Shadow driver (spec written when started)

### Milestone M5 — driver and implementer environment

- **Failing test first:** with an open `claude/loop-*` PR the driver refuses to
  start another (WIP of one); with `LOOP_ENABLED` unset the scope check fails
  every implementation branch.
- **Depends on:** M1–M4.
- A scheduled cloud task starts a fresh session per item from a trusted
  prompt, in a dedicated environment with no secrets and a minimal network
  allowlist. Merge, review and label tools are denied to implementer sessions;
  budgets and the consecutive-failure breaker are enforced by the driver;
  BLOCKED outcomes are recorded.

## PR G — L1 on approved proposals (spec written when started)

### Milestone M6 — contract runner and diff-scoped mutation

- **Failing test first:** a contract directory that collects zero tests fails
  (non-vacuity); a changed line whose mutants survive the contract tests fails.
- **Depends on:** M3, M5, and the Shadow and L0 exit criteria met.
- Extends reliability plan milestones A1 (mutation score) and A2 (frozen
  floors) rather than forking them.

## PR H — Held-out verifier and live behavioural gate (spec written when started)

### Milestone M7 — verifier outside the repository

- **Failing test first:** the verifier fails closed when the private
  repository is unreachable.
- **Depends on:** M6; a private repository and a credential sessions do not
  hold.
- Extends reliability plan C0 (profiles matrix) and C3 (grow the dataset, then
  promote the gate).

## PR I — L2 experiment (spec written when started)

### Milestone M8 — one bounded experiment

- **Failing test first:** the experiment harness rejects a candidate whose
  held-out gain sits inside the measured noise band or whose cost breaks the
  rule.
- **Depends on:** M7.
- One change per epoch, safety text frozen; stop after two epochs without
  held-out gain beyond noise at equal cost.

## Product track (optional, off the loop's critical path)

- **P1 — MangoMas port of the propose stage.** Needs evidence that reaches
  every node (`sequence` passes only the previous output), role agents as
  `mangomas.agents` plugins, and line-anchored retrieval. Scored against the
  Claude Code version on the M3c replay set.
- **P2 — Composite `loop` bodies, a `verify` node and loop budgets.** Reopens
  the ADR-0011 deferral.

## Deferred / out of scope

- Autonomous merge into `dev`, and any loop write to `qa` or `main` (ADR-0036).
- An LLM triaging Dependabot PRs.
- Loop edits to the trusted base, or widening it without amending ADR-0042.
- MangoMas on local models as the implementer (ADR-0042, alternatives).
- Requiring `eval-gate.yml` before reliability plan C0 and C3 land.
- Items whose acceptance needs edits to the root instruction pair.

## Verification

```bash
python -m pytest tests/tooling -q --no-cov     # doc contracts (M0)
make protected-paths BASE_REF=origin/dev        # M0 touches no protected path
make gate                                       # the full pre-PR chain
```
