# Spec-0033: Gate falsifiability probe (`make guard-probe`)

- **Status:** Draft
- **Linked ADR:** _none — no boundary change_
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`make gate` runs twelve targets — validate-config, lint, format-check,
typecheck, lint-imports, frontmatter, protected-paths, test, coverage,
bridge-coverage, contracts-coverage, scripts-coverage. Every one measures
structure, and **nothing establishes that any of them rejects a defect.**

The repository already knows this is the risk. `.claude/skills/mango-mutation-proof/SKILL.md`
documents the back-up / mutate / assert-failure / restore loop and catalogues
**four** silent-pass failures this repo has already shipped — 13 of 15 CLI
`monkeypatch` sites with no effect, a set-equality assertion that could not see
a reorder, and two more. `specs/TEMPLATE.md` goes further and *mandates*
two-sided scenarios: "WHEN the guarded defect is present THEN the gate fails
(prove the guard can fire) … AND WHEN it is absent THEN the gate passes."

Both are prose. Neither can fail a build. Per spec-0022 R15 — "a constraint
written as a mechanism survives agent turnover" — a mandate that no CI job
enforces is a mandate that decays. `scripts/check_coverage.py` is the worked
example: it sat at 24 % coverage, imported only for its constants, while gating
every other package. A defect there would have passed the whole per-package
gate while measuring nothing, and **no coverage number could have revealed it.**

## Requirements

- **R1** — A declarative manifest records, per guarded gate: the gate command,
  a discriminating mutation, and the expectation that the gate fails under it.
- **R2** — `scripts/guard_probe.py` applies each mutation, asserts the gate goes
  **red**, restores the tree, and asserts the gate goes **green** again. Both
  assertions are required: a gate that fails in both states proves nothing.
- **R3** — Restoration is guaranteed on any exit path, including an exception or
  an interrupt. A probe that leaves the tree mutated is worse than no probe.
- **R4** — The probe refuses to run against a dirty working tree, so a mutation
  can never be confused with, or clobber, uncommitted work.
- **R5** — A manifest entry whose mutation no longer makes its gate fail is a
  **probe failure**, not a skip. That is the disarmed-gate signal and the whole
  point of the mechanism.
- **R6** — `make guard-probe` is a CI job of its own. It is deliberately **not**
  added to `make gate`: it mutates the tree, and `gate` must stay safe to run on
  a dirty checkout.
- **R7** — No hard-coded paths or commands in the script; everything gate-specific
  lives in the manifest, so adding a guarded gate is a data change.

## Scenarios (WHEN/THEN)

- WHEN a manifest entry's mutation is applied THEN its gate command exits
  non-zero; AND WHEN the tree is restored THEN the same command exits zero.
- WHEN a mutation is applied and the gate still passes THEN the probe **fails**
  and names the disarmed entry (R5).
- WHEN the probe raises or is interrupted mid-entry THEN the tree is restored
  byte-for-byte (R3) — asserted by hashing before and after.
- WHEN the working tree has uncommitted changes THEN the probe refuses to start
  and exits non-zero (R4).
- WHEN the manifest is empty or unreadable THEN the probe fails rather than
  reporting success over zero entries — the fail-open shape `check_coverage.py`
  and `lint_agent_frontmatter.py` each had to be repaired for.

## Config / env additions

_None._ The manifest is repository data, not an operator tunable, so nothing
enters `Settings`.

## Protocol / contract impact

- New/changed protocols: _none_.
- New error types: _none_.
- Registry additions: _none_.
- Protected paths: `Makefile` and `.github/workflows/ci.yml` are deliberately
  **not** protected (ADR-0030's marker-fatigue reasoning), so adding the target
  and job needs no `BREAKING-CHANGE` trailer. Nothing under
  `[tool.mangomas.governance]` is touched by this spec.

## Backwards-compatibility

- Purely additive: a new script, a new manifest, a new `make` target, a new CI
  job. `make gate` is unchanged and still runs offline.
- The probe is opt-in locally and never runs as part of the default goal.

## Test plan

- Unit: `tests/harness/test_guard_probe.py` — a stub manifest against a stub
  gate command, driving R2–R5 with no real mutation of the repo.
- The probe's own restore path is asserted by content hash, not by absence of
  error, so a partial restore fails the test.
- Gates: the probe is itself a guard, so it gets the treatment it administers —
  a test proves it reports failure when handed a manifest whose mutation does
  not discriminate.
- Coverage: `scripts/` holds its measured `SCRIPTS_FLOOR`; the new module is
  added to `SCRIPTS_TESTS` in the `Makefile` so it is actually measured.

## Acceptance criteria

- [ ] `make guard-probe` passes on a clean tree with the seeded manifest.
- [ ] A deliberately disarmed manifest entry makes it fail (test proves it).
- [ ] The tree is byte-identical after a probe run, including after a failure.
- [ ] Any gate whose mutation cannot be made to fail it is deleted or rewritten
      — that outcome is a success of this spec, not a failure of it.
- [ ] `ruff`, `mypy --strict`, `pytest`, `check_coverage.py` clean.
- [ ] CHANGELOG updated.
