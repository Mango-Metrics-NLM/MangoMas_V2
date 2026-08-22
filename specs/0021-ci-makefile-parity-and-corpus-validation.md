# Spec-0021: CI/Makefile parity and corpus-validation completion

- **Status:** Implemented (acceptance adjudicated 2026-08-22 — all boxes verified against the shipped tree)
- **Linked ADR:** _none — no boundary change._ Same footing as spec-0020: this
  touches CI configuration, a Makefile target, docs and pytest coverage of an
  existing script's entry point — no protocol, error contract, or layering
  rule changes.
- **Linked CHANGELOG entry:** `[Unreleased]` › _CI/Makefile parity and
  corpus-validation completion — Spec-0021_

## Problem

PR #35 merged into `feat/initial-release` during the spec-0020 work, putting
the `cli/` package split on trunk. A fresh audit of the CI/Makefile/doc/corpus
surface against that new trunk state — the same "gate silently stops covering
something" defect class spec-0020 named and gave a permanent home to — found
four more instances:

| # | Defect | How it failed open |
|---|---|---|
| 1 | `ci.yml`'s `pull_request` trigger names `["main", "develop"]` | `develop` doesn't exist; `main` has genuinely diverged from trunk (`feat/initial-release`). A PR against the real trunk gets no `pull_request`-triggered CI |
| 2 | `secret-scan` is the one CI job with no Makefile target | raw inline `curl`/`gitleaks` shell, `GITLEAKS_VERSION` hardcoded only in the YAML; falsifies README's "the Makefile wraps the exact commands CI runs" claim for exactly this job |
| 3 | `AGENT_SKILL_OWNERS` values checked only by substring in agent prose | a renamed/retired skill cited only in stale prose still passes |
| 4 | The live `.claude/` corpus's Pydantic schema never runs under pytest | only `make frontmatter` (a separate, non-pytest step) or synthetic fixtures exercise it; a corrupted live frontmatter file passes all 27 existing `test_corpus_contract.py` checks |

Two doc catch-ups ride along: `docs/architecture/c2-container.md`'s `cli`
container and `README.md`'s `cli/` project-layout line both still describe the
pre-split shape, while `CLAUDE.md`'s own architecture tree already reflects the
split in full.

## Requirements

- **R1** — CI's `pull_request` trigger names this repo's actual trunk
  (`feat/initial-release`), not a nonexistent branch or a permanently-diverged
  one. Enforced by test.
- **R2** — `secret-scan` runs via a Makefile target CI calls, joining the five
  other jobs `tests/deploy/test_ci_make_parity.py` already asserts delegate to
  `make`; `GITLEAKS_VERSION` has exactly one home.
- **R3** — `AGENT_SKILL_OWNERS` values are checked against the live skill
  roster, not only cited in agent prose.
- **R4** — The live `.claude/` corpus's full schema lint (`run_schema_lint()`)
  runs under pytest, not only via the separate `make frontmatter` step.
- **R5** — `c2-container.md` and README's project-layout section describe the
  `cli/` package-facade pattern consistently with `CLAUDE.md`.
- Every new/changed guard is mutation-proven (`mango-mutation-proof` skill).
- Must remain **additive**: no runtime behaviour change. The only `src/`-tree
  edits in this spec's scope are test functions under `tests/`; nothing in
  `src/mangomas/` changes.

## Config / env additions

**None.** `GITLEAKS_VERSION` is a Makefile variable, not a `MANGOMAS_*`
setting — gitleaks is a CI/dev-tooling concern, not application configuration,
so it does not belong in `Settings`.

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_
- Registry additions: _none_

## Backwards-compatibility

- **No runtime behaviour changes at all.** Every edit in this spec touches CI
  YAML, the Makefile, tests, or prose docs.
- `make gate`'s chain and behaviour are unchanged; `secret-scan` stays
  deliberately outside it (see Test plan) — no existing target's meaning
  changes.
- `README.md`'s "the Makefile wraps the exact commands CI runs" claim becomes
  true after R2, rather than needing to be walked back.

## Test plan

- Unit: `tests/deploy/test_ci_make_parity.py` gains two assertions (R1's PR
  trigger, R2's `secret-scan` delegation). `tests/tooling/test_corpus_contract.py`
  gains two tests (R3's `AGENT_SKILL_OWNERS` resolution, R4's live schema lint).
- Gated (external SDK): _none._ `make secret-scan` needs network (downloading a
  pinned gitleaks release binary) but is not part of `make gate`, mirroring why
  `integration`/`lmstudio`/`vertex`/etc. are kept as separate opt-in targets —
  every other step in `gate`'s chain runs fully offline.
- Coverage: the 95% global gate and every per-package floor hold unchanged;
  nothing in this spec touches `src/mangomas/`.
- **Every new guard is mutation-proven**: R1's guard by reverting the trigger
  and confirming failure; R2's by breaking the job's delegation; R3's by
  pointing an `AGENT_SKILL_OWNERS` entry at a nonexistent skill slug; R4's by
  corrupting one skill's frontmatter YAML and confirming a named, readable
  failure rather than a silent pass or an opaque collection error.

## Acceptance criteria

- [x] R1: `ci.yml`'s `pull_request` trigger targets `["feat/initial-release"]`
      only; a revert to `["main", "develop"]` fails the new test.
- [x] R2: `secret-scan` delegates to `make secret-scan`; `GITLEAKS_VERSION`
      appears in exactly one place.
- [x] R3: every `AGENT_SKILL_OWNERS` value resolves to a real skill slug.
- [x] R4: `run_schema_lint()` runs against the live `.claude/` tree under
      pytest and reports zero failures.
- [x] R5: `c2-container.md` and `README.md` both name the `cli/` facade pattern.
- [x] `ruff`, `mypy`, `pytest` (95% gate + per-package floors),
      `frontmatter-lint`, `protected-paths` all clean — `make gate` green at
      every commit.
- [x] No protected path touched; no `BREAKING-CHANGE` trailer required.
- [x] CHANGELOG updated. No ADR required (no boundary changed).

> Acceptance adjudicated 2026-08-22 against the shipped tree (roadmap Phase 0.4); unchecked boxes remain genuinely open.

## Deliberate non-goals

- **`main`/`feat/initial-release` reconciliation.** Tracked separately in
  NEXT_STEPS.md as its own, larger effort — 100 commits diverged one way, 11
  the other. This spec fixes the CI trigger's *drift*, not the branches'
  divergence.
- **A new "regression"/"AQA" test scaffold.** No artifact by either name exists
  in this repo today (confirmed by direct search); `make gate` plus the full
  pytest suite already is the regression pass, and inventing a parallel concept
  would be an unrequested abstraction.
- **Reorganizing CHANGELOG's `[Unreleased]` section.** ~1160 lines today; a
  separate cleanup, not touched here.
- **A `cli` component-level (C3) diagram.** `c3-component.md` is scoped to
  `api/` and doesn't cover `cli` at all; adding one is a bigger, separately-
  scoped addition, not a catch-up edit.
- **numpy.** Zero dependency anywhere in this repo (`pyproject.toml`, `src/`,
  `tests/` all confirmed clean). Does not apply to this codebase.

## Known limitation

Same one spec-0020 already recorded: agent/skill *utilization* is unmeasured.
This spec's R3/R4 make the corpus's *validity* checkable in one more way; they
say nothing about whether any of the 25 agents or 15 skills are actually
invoked in practice.
