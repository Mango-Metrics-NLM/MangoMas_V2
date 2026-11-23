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
branch, two branch classes, fail-closed, plus a recorded answer to who the
loop acts as and with what rights. M2 and M3 give the loop its input: a
machine-readable backlog and the propose stage that fills it with approved
items. M4 makes the loop measurable: a ledger derived from GitHub and CI, a
current-process baseline and the false-accept bound. Only then does M5 start the Shadow
driver; M6 opens L1 with diff-scoped mutation; M7 adds held-out and live
behavioural checks; M8 runs L2 as an experiment. The constraint that shaped
the order: **nothing the loop does is trusted until something it cannot
influence has checked it**, so verification, input and measurement land
before autonomy.

Analysis and rationale:
[deep dive](../analysis/20261123-self-improvement-loop-deep-dive.md).
Milestones are numbered across PRs (M0–M8) rather than per PR as in the
template, because they span four specs.

## PR A — Decision records (M0)

### Milestone M0 — analysis, plan, ADR-0042, specs 0052–0055

- **Failing test first:** none — docs only. `tests/tooling/test_decision_record_numbering.py`
  and `tests/tooling/test_doc_links.py` must stay green.
- **Depends on:** nothing.
- Lands the deep dive, this plan, ADR-0042 (Proposed), four Draft specs, index
  rows in `specs/README.md`, a `NEXT_STEPS.md` overlay and a CHANGELOG entry.

## PR B — Trusted base and scope check (spec-0052)

Owners: `mango-harness-dev` (policy, checker) and `mango-ci-dev` (workflow,
Makefile). Touches `pyproject.toml` and `src/mangomas/harness/governance.py`,
both protected, so commits carry one scoped trailer per path, for example
`BREAKING-CHANGE: pyproject.toml — add the loop trusted-base policy (ADR-0042)`.

### Milestone M1a — policy and checker

- **Failing test first:** `tests/test_check_loop_scope.py` — an implementation
  branch whose diff touches `Makefile`, adds a nested `ruff.toml` or adds an
  unallowed `# type: ignore` fails; a second open implementation PR fails; a
  proposal branch touching `src/` or editing an existing ADR fails; a governed
  head from a fork fails; an ungoverned branch passes; a missing policy fails
  `claude/**` heads only. Red until `scripts/check_loop_scope.py` exists.
- **Depends on:** ADR-0042 accepted.
- `[tool.mangomas.governance.loop]` in `pyproject.toml`;
  `scripts/check_loop_scope.py` (stdlib only, reusing `scripts/_governance.py`)
  with its tests in `SCRIPTS_TESTS`; `make loop-scope`; both new files added to
  `protected_paths` and `GOVERNANCE_SURFACE`.

### Milestone M1b — anchored workflow and required checks

- **Failing test first:** `tests/deploy/test_workflow_hardening.py`
  (`_EXPECTED_WORKFLOW_COUNT` 6 → 7) plus a new assertion that
  `loop-scope.yml` uses `pull_request_target`, never checks out a PR ref,
  grants only `contents: read` and `pull-requests: read`, and keys its
  concurrency group per PR.
- **Depends on:** M1a.
- `.github/workflows/loop-scope.yml`. `CONTRIBUTING.md`'s required-check list
  (its column renamed "Job name (workflow)") gains `Guard falsifiability
  probe` and `Loop scope check`; an admin marks both required on `dev`, `qa`
  and `main`, with the source pinned to GitHub Actions.

### Milestone M1c — mutation proof

- **Failing test first:** `MUT-LOOP-*` guard-probe entries turn the scope tests
  red when a trusted-base glob is removed, fail-closed is flipped, a truncated
  listing is accepted, the suppression rule is dropped, or the open-PR check
  is skipped.
- **Depends on:** M1a.

### Milestone M1d — identity and credential (gates M5)

- **Failing test first:** none is possible inside this repository; this is a
  recorded verification.
- **Depends on:** M1b.
- Settle spec-0052 R2 (a dedicated loop identity, or a verified branch
  assignment) and R10 (the implementer's credential has no write access to
  workflows, statuses, checks, variables or administration, and no ruleset
  bypass). Record both results in spec-0052. M5 does not start until they are
  recorded.

## PR C — Machine-readable backlog (spec-0053)

### Milestone M2 — schema, validator, contract runner, seed items

- **Failing test first:** `tests/test_check_backlog.py` — an item whose
  `allowed_paths` leaves the work area, a dependency cycle, or a
  contract-digest mismatch fails; `make contract ITEM=<ID>` fails when it
  collects zero tests.
- **Depends on:** M1a (the validator reads the same policy).
- `backlog/<ID>.toml` items and `backlog/README.md`; `scripts/check_backlog.py`
  with its tests in `SCRIPTS_TESTS`; `make backlog-check` in `make gate` and
  the `lint` CI job; pytest and mypy exclusions for `tests/contract/` (a
  `pyproject.toml` trailer); `make contract ITEM=<ID>` and activation markers
  under `tests/contract/active/`; three to five Shadow seed items written by
  hand.

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
- `scripts/build_loop_ledger.py`; `make loop-metrics`; the current-process
  escaped-defect baseline over six months of trunk history (`dev`, previously
  `feat/initial-release`).

## PR F — Shadow driver (spec written when started)

### Milestone M5 — driver and implementer environment

- **Failing test first:** with `LOOP_ENABLED` not `true` the driver starts no
  session; with an item's wall-clock budget spent the driver stops it.
- **Depends on:** M1–M4, with M1d's results recorded.
- A scheduled cloud task starts a fresh session per item from a trusted
  prompt, in a dedicated environment with no secrets and a minimal network
  allowlist, acting through the identity and credential recorded in M1d. The
  driver handles liveness only (when to start a session, wall-clock budgets);
  every safety limit belongs to the scope check. BLOCKED outcomes are
  recorded.

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
- Items that need companion edits in the trusted base (spec-0053 R6).
- The repair sandbox's staging hole (a staged `conftest.py` reaches a pytest
  oracle): real, but on the ADR-0039/0040 line, since the loop does not use
  the sandbox.

## Verification

```bash
python -m pytest tests/tooling -q --no-cov     # doc contracts (M0)
make protected-paths BASE_REF=origin/dev        # M0 touches no protected path
make gate                                       # the full pre-PR chain
```
