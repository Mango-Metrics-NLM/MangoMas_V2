# Spec-0053: Machine-readable backlog

- **Status:** Draft
- **Linked ADR:** [ADR-0042](../docs/adr/0042-governed-self-improvement-loop.md) (Proposed)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Governed self-improvement loop: decision records (ADR-0042 / spec-0052..0055)`
- **Delivery plan:** [milestone M2](../docs/plans/20261123T000000Z-governed-self-improvement-loop-plan.md)

## Problem

The loop needs input it can parse. `NEXT_STEPS.md` is over 900 lines of dated
prose with no stable item IDs, dependency fields or acceptance criteria, and
plan milestones carry "Failing test first" and "Depends on" as prose bullets
(`docs/plans/_template.md`). Without items a machine can read, "the top
unblocked item" is the agent's own interpretation, which is another
self-report. And acceptance tests written before the code cannot simply sit in
`tests/`: `testpaths` and mypy's `files` both include it
(`pyproject.toml:279,335`), so a red-first test would turn `dev` red, and
skips and xfails are banned (`CONTRIBUTING.md:166-168`).

## Requirements

- **R1 One file per item.** `backlog/<ID>.toml`, read with the stdlib
  `tomllib`, plus a `backlog/README.md` the validator ignores. One file per
  item keeps approvals independent and merge conflicts rare. Items are
  definitions: written by a human, or added on a proposal branch
  (spec-0054), and approved by merging. They are never edited afterwards
  (spec-0052 R5) and never hold run state; status lives in the ledger
  (spec-0055).
- **R2 Fields.** Unknown keys are rejected.

  | Field | Type | Rule |
  |---|---|---|
  | `id` | string | `LOOP-NNNN`, equal to the filename stem, never reused |
  | `title` | string | One line |
  | `kind` | enum | `maintenance`, `feature` or `experiment` |
  | `min_stage` | enum | `shadow`, `l0`, `l1` or `l2` |
  | `priority` | int | Lower runs first; ties break on `id` |
  | `source` | string | The `NEXT_STEPS.md` overlay, plan milestone or proposal it came from |
  | `depends_on` | list of IDs | Each must exist; no cycles |
  | `allowed_paths` | list of globs | Each inside the work area (spec-0052) |
  | `allowed_suppressions` | list | Suppression comments the diff may add (spec-0052 R4); empty by default |
  | `behaviour_preserving` | bool | When true, the existing suite must pass unchanged and no contract directory is required |
  | `contract_sha256` | string | Required unless `behaviour_preserving`: SHA-256 over the sorted (relative path, LF-normalised bytes) pairs of every file in the item's contract directory |
  | `max_diff_lines` | int | Default 300; enforced by the scope check |
  | `budget` | table | `max_attempts` (enforced by the scope check) and `max_wall_minutes` (enforced by the driver) |

- **R3 Validator.** `scripts/check_backlog.py` (stdlib only) and
  `make backlog-check`, wired into `make gate` and the `lint` CI job, with its
  tests in `SCRIPTS_TESTS`. It checks the schema, ID and filename agreement,
  unique IDs, an acyclic dependency graph, that every `allowed_paths` entry's
  literal prefix (up to its first wildcard) lies under a work-area root, and
  that each contract directory exists with a matching digest. If the shared
  policy reader lands in `scripts/_governance.py`, a protected path, that commit
  carries its own scoped trailer.
- **R4 Selection versus enforcement.** An item with a merged implementation
  PR is done and never selected again. From the items not yet done, the
  driver picks the unblocked one with the lowest (`priority`, `id`) whose
  `min_stage` is at or below the loop's stage; when no undone item is
  unblocked, the loop stops. Picking is a convenience: the scope check
  independently enforces completion, dependencies, stage, diff size, attempts
  and work in progress (spec-0052 R3).
- **R5 Contract tests.**
  - *Location:* `tests/contract/<id_snake>/`, where `<id_snake>` is the ID
    lowercased with `-` replaced by `_` (`loop_0007`), so it is a valid package
    name.
  - *Exclusion:* M2 adds (in a human PR carrying the `pyproject.toml` trailer)
    exclusions so the default pytest run and mypy skip `tests/contract/`. A
    red-first contract test then never turns `dev` red, and no skip or xfail is
    needed.
  - *Running:* `make contract ITEM=<ID>` collects, runs and type-checks one
    directory explicitly; collecting zero tests fails. CI runs it for the
    implementation branch of that item.
  - *Red first:* on the proposal branch, CI runs the same target and requires
    the tests to be collected and to fail. A contract that already passes
    before implementation proves nothing about the change and is rejected.
  - *Activation:* after the implementation merges, a proposal branch adds the
    marker file `tests/contract/active/<id_snake>` (a new file), and from then
    on `make gate` runs and type-checks every active directory.
- **R6 Companion edits are out of scope in the first version.** Tests bind
  some trusted-base files to `src/`. An item whose change needs any of these
  edited stays human work, and the propose stage routes it there:
  - `tests/snapshots/openapi.json` (the HTTP surface);
  - `tests/test_import_compat.py` (facade completeness for the config, CLI,
    composition and telemetry facades);
  - directory `CLAUDE.md` files checked by `tests/tooling/test_directory_claude_md.py`;
  - the source-owner roster in `tests/tooling/test_corpus_contract.py` (a new
    top-level entry under `src/mangomas`);
  - guard-probe snippets the change would alter (`tests/harness/test_guard_probe.py`);
  - the configuration documentation (`AGENTS.md`, `.env.example`).
- **R7 Seed.** Three to five Shadow items written by hand, each
  behaviour-preserving, inside the work area and free of companion edits. For
  example:
  - extract private helpers from `src/mangomas/adapters/llm/vertex/_client.py`
    (439 lines) into a private sibling module, changing no public import path;
  - a lint-family rollout for one package: the contract test runs
    `ruff check --select <FAMILY>` on that package, and only after the
    implementation merges does a human enable the rule in `pyproject.toml`.
- `NEXT_STEPS.md` stays the human roadmap; an item cites it in `source`.

## Scenarios (WHEN/THEN)

- WHEN an item's `allowed_paths` includes `Makefile` or `tests/` THEN
  `backlog-check` fails and names the entry; WHEN every entry lies under a
  work-area root THEN it passes.
- WHEN two items depend on each other THEN it fails; WHEN the dependencies form
  a DAG THEN it passes.
- WHEN a contract file changes after approval THEN the digest check fails.
- WHEN `backlog/` is missing THEN the validator fails, so a moved directory
  cannot validate nothing; WHEN it holds only its README THEN it passes and
  reports zero items.
- WHEN `make contract ITEM=<ID>` collects zero tests THEN it fails; WHEN the
  contract tests pass THEN it passes.
- WHEN a proposal's contract tests already pass before implementation THEN the
  proposal is rejected; WHEN they fail THEN it passes.
- WHEN an item already has a merged implementation PR THEN the driver does not
  select it; WHEN every remaining item is done or blocked THEN the loop stops.
- WHEN an item's contract directory is unimplemented THEN `make gate` on `dev`
  stays green; WHEN its activation marker exists THEN `make gate` runs it.

## Config / env additions

None in `Settings`. The file format is the contract.

## Protocol / contract impact

- New/changed protocols: none.
- New error types: none.
- Registry additions: none.

## Backwards-compatibility

- A new directory, two make targets and two exclusions in `pyproject.toml`;
  nothing existing changes behaviour.

## Test plan

- Unit: `tests/test_check_backlog.py`, both directions for every scenario.
- Gates: a `MUT-BACKLOG-01` guard-probe entry; a validator that accepts an
  entry outside the work area must turn it red.

## Acceptance criteria

- [ ] Every scenario is a test, in both directions.
- [ ] `make backlog-check` runs inside `make gate` and in CI.
- [ ] Seed items validate, stay inside the work area and need no companion
      edit.
- [ ] CHANGELOG updated, naming the tests that lock this in.
