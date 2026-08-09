# Spec-0018: Live Claude Code corpus

- **Status:** In progress
- **Linked ADR:** ADR-0024 (Live corpus: surface choice and permission posture)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Changed`

## Problem

19 agent definitions live under `.github/agents/` and 12 skills under
`.github/skills/`. **Claude Code reads nothing from `.github/`**, so none of this
corpus loads in a Claude Code session.

The corpus is *not* an invented format — a common mis-diagnosis worth recording so
it is not repeated. `.github/agents/**/*.agent.md` and `.github/skills/<name>/SKILL.md`
are real, documented **GitHub Copilot** surfaces: `tools: [read, edit, search, execute]`
are all four documented Copilot aliases, and all 12 skills are fully conformant. The
corpus works today — for a tool this project does not use.

Moving to `.claude/` is not a trade. VS Code Copilot *also* scans `.claude/agents/`
(`.md`) and `.claude/skills/`, so a single `.claude/` corpus serves Claude Code **and**
VS Code Copilot; only the github.com cloud-agent surface is given up. Copilot cannot
discover nested `.github/agents/<parent>/<child>` either (upstream feature request), so
15 of the 19 agents are invisible to Copilot today as well — the move fixes that for
both tools.

| Field | Copilot | Claude Code |
|---|---|---|
| `tools: [read, edit, search, execute]` | valid aliases | invalid — needs `Read`, `Edit`, `Bash`, … |
| `model: Claude Sonnet 4.5 (copilot)` | real field, undocumented value | invalid — needs `sonnet`/`inherit`/full ID |
| `sub_agents:` | near-miss (real key is `agents:`) | not a field — and there is no working replacement (see below) |
| `argument-hint` on agents | valid in VS Code, ignored on github.com | not an agent field (skills only) |
| nested `<parent>/<child>` | not discovered | discovered (recursive) |

**Correction (recorded during implementation).** An earlier draft of the row
above said the parent/child relationship could be re-expressed as
`tools: Agent(a, b)`. It cannot: that scoping form works on the main thread's
`--agent` flag and is **silently ignored inside a subagent definition** — the
agent receives *unrestricted* delegation rather than the named subset — and
`Agent` is absent from the background-subagent tool set, which is the default.
The hierarchy therefore has no working replacement, so the corpus is flat and
the four former parents became routers that advise rather than delegate. The
frontmatter lint rejects the `Agent(...)` form outright, because a rule that
looks like a restriction and is not is worse than no rule.

Independently, the gate that is supposed to validate this corpus **cannot detect its own
irrelevance**. `scripts/lint_agent_frontmatter.py::main` globs both trees and, when zero
files match, falls through to `logger.info("Frontmatter lint passed")` and returns
`EXIT_OK`. The counts are passed via `extra={}`, which the configured log format drops,
so they are never printed. `test_main_returns_ok_on_clean_repo` asserts only
`main([]) == EXIT_OK`, and no test anywhere asserts a non-zero file count. **A naive
`git mv` therefore produces a green CI that validates nothing** — the same
quietly-passing-gate failure ADR-0021 was written to eliminate.

## Requirements

- R1 — A non-empty-corpus guard lands **before** any file moves, so the move is verified
  rather than assumed. The floor is structural (`>= 1`), not a roster count: the script's
  honest claim is "a non-empty corpus", and a floor that tracks the expected roster would
  need a human to remember to bump it on every legitimate addition.
- R2 — `main()` must expose its counts. Today it returns only an `int`, so no test *can*
  assert on discovery. Extract a `LintResult` and print the counts in the log **message**.
- R3 — Each tree moves in a **content-free** commit. Editing files in the same commit as
  the move risks git rename detection failing (50 % similarity threshold), which breaks
  `git log --follow` permanently and turns every in-flight branch's rebase into
  delete-vs-modify conflicts.
- R4 — The documentation sweep ships **with** the move, not after. `CLAUDE.md` auto-loads
  into every session, so a stale pointer there is worse than one anywhere else in the repo.
- R5 — A corpus contract test asserts the roster by **set equality**, not by count: a count
  names nothing, whereas a roster names the file that appeared or vanished, and the
  one-line constant edit that resolves it is the review record.
- R6 — Agents (a later PR under this spec) declare `tools` explicitly. Omitting `tools`
  makes a subagent inherit **every** tool, including `Bash`/`Edit`/`Write`.
- R7 — Skills own procedure; agents carry only ownership, routing boundary, and output
  format. Enforced by a test — the current corpus has agents that say *"use the X skill
  for the full recipe"* and then restate the recipe, including the same stale pointer in
  both copies.
- Must remain **additive** for anything outside the corpus: no runtime behaviour changes,
  no `src/mangomas/` changes.

## Config / env additions

_None._ Corpus location and lint floors are development tooling, not application runtime
behaviour — consistent with spec-0016's stance that pure dev-tooling config stays out of
`Settings`. The lint floors are script-level `Final` constants with CLI-flag overrides.

## Protocol / contract impact

- New/changed protocols: _none_
- New error types: _none_
- Registry additions: _none_

## Backwards-compatibility

- No `src/mangomas/` source file changes; runtime behaviour is byte-identical.
- `make frontmatter`, `make gate`, and the CI `lint` job keep their names and semantics;
  only the paths they validate change.
- The legacy `--check-protected-paths` flag and both `--hook` modes are untouched.
- **Deprecation, stated explicitly:** the github.com Copilot cloud-agent surface is given
  up. VS Code Copilot support is retained via `.claude/`. Recorded in CHANGELOG under
  `Changed` (not `Removed`) because the capability moves rather than disappears.

## Test plan

- Unit: `tests/test_lint_agent_frontmatter.py` extended for the floor (empty tree →
  `EXIT_SCHEMA`; real repo non-zero; flag overrides; `caplog` sees the counts).
- New `tests/tooling/test_corpus_contract.py` — roster set-equality, directory/`name`
  agreement, and (in the agents PR) slug validity, tool vocabulary, delegation-graph
  resolution, and inverted `path::Symbol` traceability with non-vacuity floors.
- Adversarial verification: one mutation per contract test, confirming *that named test*
  fails. Two tests in an earlier draft of this spec would never have fired.
- Coverage: maintain the 95 % global gate. `tests/tooling/` imports no `mangomas` code so
  it moves neither the global floor nor any per-package floor; new validator branches in
  `scripts/` need coverage in `tests/test_lint_agent_frontmatter.py` for
  `make scripts-coverage`'s floor.

## Acceptance criteria

- [ ] The frontmatter gate fails when its globs match zero files, and a test proves it.
- [ ] `make frontmatter` prints non-zero skill and agent counts in its message.
- [ ] Every skill resolves under `.claude/skills/<name>/SKILL.md`; `.github/skills` is gone.
- [ ] Roster set-equality holds, and deleting one corpus file fails a *named* test.
- [ ] No documentation references a corpus path that no longer exists.
- [ ] `ruff`, `mypy`, `pytest` (95 % gate), `frontmatter-lint` all clean — `make gate` green.
- [ ] CHANGELOG updated; ADR-0024 accepted.
