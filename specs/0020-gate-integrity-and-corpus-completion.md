# Spec-0020: Gate integrity and corpus completion

- **Status:** In progress
- **Linked ADR:** _none — no boundary change._ The gates and the corpus are
  project infrastructure; nothing here alters a protocol, an error contract, or
  a layering rule. ADR-0019's amendment already records the measurement lesson
  this spec generalises.
- **Linked CHANGELOG entry:** `[Unreleased]` › _Gate integrity — Spec-0020_

## Problem

Four separate defects of one shape have now been found in this repo's quality
gates, three of them inside a single week:

| # | Defect | How it failed open |
|---|---|---|
| 1 | `api/*.py` floor glob stayed flat when `api/` grew `routes/` | new routers unmeasured, gate green |
| 2 | `cli` floor glob non-recursive ahead of the spec-0015 split | would have unmeasured the whole command package |
| 3 | `exclude_lines` carried an unanchored `"\.\.\."` | whole Typer command bodies dropped; `rag.py` reported 16 statements where the parser sees 53 |
| 4 | No floor-completeness guard | a new top-level package gets no floor and nothing says so |

They share one signature: **a gate config that stops covering something and
reports success anyway.** Nothing about a green gate distinguishes "we checked
everything and it passed" from "we checked less than you think and it passed."
Defect 3 is the sharpest illustration — an over-matching exclusion makes the
coverage percentage go *up*, because the lines it swallows are the untested
ones, so the symptom is indistinguishable from an improvement.

Defects 1–3 are fixed. This spec fixes 4, and — more importantly — gives the
class a **name and a single home**, so the fifth instance is caught by an
existing guard instead of being rediscovered by accident.

Two adjacent gaps surfaced in the same audit and are folded in because they are
the same "is it actually wired" question: `mypy`'s checked surface differs
between the documented developer command and the gate (155 vs 324 files), and
five source surfaces have no write-capable agent owner.

## Requirements

- **R1** — `scripts/check_coverage.py`'s floor list must be *provably complete*:
  every top-level entry under `src/mangomas/` is covered by a named floor, or
  appears on an explicit, justified allowlist. Enforced by test, not review.
- **R2** — The fail-open invariant class is named in one docstring, in the module
  that already holds three of its four guards
  (`tests/test_check_coverage.py`), so the next instance has an obvious home.
- **R3** — A bare `mypy` must check the same surface as `make typecheck`. The
  drift is removed in configuration, not documented as a caveat, and locked by
  the test module that already binds CI↔Makefile.
- **R4** — Adopt the ruff rule families measured at zero violations across **all
  four** lint paths (`src tests scripts eval_harness_bridge/src`), plus those
  whose only hits are trivially auto-fixable. Families with real churn and no
  defect behind them are recorded as non-goals, not landed.
- **R5** — Every source surface has exactly one write-capable agent owner
  (spec-0018 R7: no file claimed by two).
- **R6** — Procedures that produced findings and were executed repeatedly become
  skills, since skills own procedure and agents own surface.
- **R7** — Must be **additive**. No runtime behaviour changes: this spec touches
  gate configuration, tests, the `.claude/` corpus and hooks only. `src/`
  changes are limited to auto-fixable lint (`C420`, `PTH123`), which are
  semantically identical rewrites.

## Config / env additions

**None.** This spec adds no tunable. The existing
`MANGOMAS_DISABLE_RTK_HOOK` remains the only hook-level opt-out.

Gate thresholds stay where they already live — `scripts/check_coverage.py`
(`FLOORS`, `GLOBAL_FLOOR`) is the single source of truth for coverage floors,
mirrored by the `--cov-fail-under` addopt and locked by
`tests/deploy/test_ci_make_parity.py`. No floor value changes here; one floor is
*added* (`_entry_points.py`, already measured at 100%).

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_
- Registry additions: _none_

Corpus additions (not runtime contracts): two agents — `mango-cli-dev`,
`mango-harness-dev` — and two skills — `mango-mutation-proof`,
`mango-coverage-audit`. Roster moves 23 → 25 agents and 13 → 15 skills, both
pinned by `tests/tooling/test_corpus_contract.py`.

## Backwards-compatibility

- **No runtime behaviour changes at all.** The public API, CLI surface, exit
  codes, HTTP routes and import paths are byte-identical.
- Adopting lint families is a **ratchet on new code**, not a rewrite: eight of
  the nine are at zero violations across every linted path, and the three
  remaining hits are `ruff --fix`-clean, semantics-preserving rewrites.
- `ASYNC240` is excluded by name and with its reason. It recommends
  `trio.Path`/`anyio.path`; this project is asyncio-only, and all four hits are
  synchronous `Path.read_text()` inside async *test assertions*, which is
  correct. Left unexcluded, the rule would pressure a nonsensical dependency
  change — recorded so a future contributor or auto-fixer does not "fix" it.
- The `mypy` change **widens** what a developer sees locally to match CI. It
  cannot break the gate, which already checked the wider surface.

## Test plan

- Unit: `tests/test_check_coverage.py` gains the R1 completeness guard beside
  its three siblings, reusing the existing `_all_floors()`, `_FloorLike` and
  `_REPO_ROOT` helpers. `tests/deploy/test_ci_make_parity.py` gains the R3
  Makefile↔pyproject mypy-surface assertion.
- Corpus: `tests/tooling/test_corpus_contract.py` roster constants updated for
  the two agents and two skills; `make frontmatter` validates their frontmatter.
- Hooks: `tests/constants.py::PREEXISTING_HOOKS` and
  `tests/tooling/test_claude_code_settings.py` — that contract exists precisely
  to make hook edits deliberate.
- Gated (external SDK): _none — this spec adds no SDK dependency._
- Coverage: the 95% global gate and every per-package floor hold unchanged. The
  `cli` package must stay at the measured 100% that PR #35 established.
- **Every new guard is mutation-checked**, per the standard this repo now holds
  itself to: the R1 guard is proven by creating a temporary package under
  `src/mangomas/` and confirming it fails.

## Acceptance criteria

- [x] R1: a new top-level package with no floor fails the suite (mutation-proven).
- [x] R2: the invariant class is named once, where its guards live.
- [x] R3: bare `python -m mypy` and `make typecheck` report the same file count.
- [x] R4: the nine families are clean across all four lint paths;
      `ASYNC240`'s exclusion is load-bearing (removing it re-surfaces 4 hits).
- [x] R5: every surface under `src/mangomas/` has a write-capable owner, or
      is recorded in `UNOWNED_SOURCE_SURFACES` (`registry.py` — a generic
      `Registry[T]` consumed equally by five registries; naming any one
      owner would be arbitrary). Locked by
      `test_every_source_surface_has_a_write_capable_owner`.
- [x] R6: both skills validate against `make frontmatter` and the corpus contract.
- [x] `ruff`, `mypy`, `pytest` (95% gate + per-package floors),
      `frontmatter-lint`, `protected-paths` all clean — `make gate` green at
      every commit, not only the last.
- [x] CHANGELOG updated. No ADR required (no boundary changed).

> Acceptance re-adjudicated 2026-09-10: R5 is met with the documented
> `registry.py` exception in `UNOWNED_SOURCE_SURFACES` (see NEXT_STEPS).
> File-exclusive ownership inside partitioned packages (api/, adapters/,
> core/) lives in each agent's Surface table, not a second roster.

## Deliberate non-goals

Recorded so they are not re-proposed without the reasoning:

- **`N` / `N818`.** Would demand renaming `AgentNotFound` → `AgentNotFoundError`
  and four siblings. That is a breaking change to the public error taxonomy, on
  a **protected path** (`src/mangomas/errors.py`), for a naming convention. The
  taxonomy is documented in CLAUDE.md and mapped in `api/errors.py::_ERROR_STATUS`;
  the rename buys nothing and costs every consumer.
- **`TRY` (96 hits)** and **`FBT` (18)**. Real churn with no defect behind them.
  Revisit only alongside a change that already touches those call sites.
- **`ASYNC240`** — see Backwards-compatibility.

## Known limitation

Agent and skill *utilization* in this repo is unmeasured. R5 and R6 rest on the
spec-0019 precedent — one owner per surface, skills own procedure — not on
evidence that the existing 23 agents are invoked in practice. If the corpus is
to keep growing, measuring which agents and skills actually get used is the
cheaper next move than adding more.
