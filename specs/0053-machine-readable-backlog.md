# Spec-0053: Machine-readable backlog

- **Status:** Draft
- **Linked ADR:** [ADR-0042](../docs/adr/0042-governed-self-improvement-loop.md) (Proposed)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Governed self-improvement loop: decision records (ADR-0042 / spec-0052..0055)`
- **Delivery plan:** [milestone M2](../docs/plans/20261123T000000Z-governed-self-improvement-loop-plan.md)

## Problem

The loop needs input it can parse. `NEXT_STEPS.md` is 948 lines of dated prose
with no stable item IDs, dependency fields or acceptance criteria, and plan
milestones carry "Failing test first" and "Depends on" as prose bullets
(`docs/plans/_template.md`). Without items a machine can read, "the top
unblocked item" is the agent's own interpretation, which is another
self-report.

## Requirements

- **R1 One file per item.** `backlog/<ID>.toml`, read with the stdlib `tomllib`.
  One file per item keeps approvals independent and merge conflicts rare. Items
  are definitions: written by a human or drafted by the propose stage
  (spec-0054), and approved by merging them. They never hold run state; status
  lives in the ledger (spec-0055).
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
  | `allowed_paths` | list of globs | Must not intersect the trusted base (spec-0052) |
  | `behaviour_preserving` | bool | When true, the existing suite must pass unchanged |
  | `contract_tests` | list of pytest node IDs | Under `tests/contract/<id>/`; required unless `behaviour_preserving` |
  | `contract_sha256` | string | Digest over the contract files' bytes, in path order |
  | `max_diff_lines` | int | Size cap on the implementation diff (default 300) |
  | `budget` | table | `max_attempts` and `max_wall_minutes` |

- **R3 Validator.** `scripts/check_backlog.py` (stdlib only) and
  `make backlog-check`, wired into `gate`: schema, ID and filename agreement,
  unique IDs, an acyclic dependency graph, `allowed_paths` disjoint from the
  trusted base (using spec-0052's policy reader), and contract files present
  with a matching digest.
- **R4 Deterministic selection.** An item is unblocked when every `depends_on`
  item has a merged outcome in the ledger. The driver picks the unblocked item
  with the lowest (`priority`, `id`) whose `min_stage` is at or below the
  loop's current stage. An empty unblocked set stops the loop.
- **R5 Seed.** Three to five Shadow items written by hand, each
  behaviour-preserving and outside the trusted base: for example, a lint-family
  rollout for one package after a human enables the rule for it, or a
  decomposition of `src/mangomas/config/__init__.py` with facade-identity
  contract tests.
- `NEXT_STEPS.md` stays the human roadmap; an item cites it in `source`.

## Scenarios (WHEN/THEN)

- WHEN an item's `allowed_paths` includes `Makefile` THEN `backlog-check` fails
  and names the glob; WHEN every path is outside the trusted base THEN it
  passes.
- WHEN two items depend on each other THEN it fails; WHEN the dependencies form
  a DAG THEN it passes.
- WHEN a contract test file changes after approval THEN the digest check fails
  until the item is re-approved with the new digest.
- WHEN `backlog/` is missing THEN the validator fails, so a moved directory
  cannot validate nothing; WHEN it exists and is empty THEN it passes and
  reports zero items.

## Config / env additions

None in `Settings`. The file format is the contract.

## Protocol / contract impact

- New/changed protocols: none.
- New error types: none.
- Registry additions: none.

## Backwards-compatibility

- A new directory and make target; nothing existing changes.

## Test plan

- Unit: `tests/test_check_backlog.py`, both directions for every scenario.
- Gates: a `MUT-BACKLOG-01` guard-probe entry; a validator that accepts an
  intersecting glob must turn it red.

## Acceptance criteria

- [ ] Every scenario is a test, in both directions.
- [ ] `make backlog-check` runs inside `make gate`.
- [ ] Seed items validate and none touches the trusted base.
- [ ] CHANGELOG updated.
