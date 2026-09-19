# Agent instruction files — delivery plan

- **Branch:** `claude/agent-md-documentation-u08et8` (plan + analysis only); one branch per PR block below
- **Date:** 2026-09-19
- **Revision:** **second pass — the first pass's central design is withdrawn.**
  Pass 1 proposed 60 per-directory `AGENTS.md` files. Probe 9 in
  [`docs/analysis/20260919-agents-md-corpus-peer-review.md`](../analysis/20260919-agents-md-corpus-peer-review.md)
  runs that exact design against `claude` 2.1.278 and the nested files **do not
  load**. All 60 would have been invisible — the same defect that retired the
  `agent.md` corpus, at sixty times the scale. This pass keeps what survived
  review and drops the rest.
- **Target release:** rolling
- **Status:** Draft — PR 1 is ready to write; PR 2 is a citation, not a new design
- **Specs:** none owed. Pass 1 owed `spec-0035`; nothing that survives needs it.
- **ADRs:** `ADR-0035` owed by **PR 2 only**, and scoped to the root-split
  boundary. Pass 1 proposed an ADR reversing the `agent.md` retirement —
  **withdrawn**; nothing here reverses it.
- **Source:** [`docs/analysis/20260919-agents-md-corpus-peer-review.md`](../analysis/20260919-agents-md-corpus-peer-review.md)
  (10 probes, 3 specialist reviews, suite executed: 3029 passed / 66 skipped)

## Status — 2026-09-19

**PR 1 delivered in full (M1.1–M1.4).** `3040 passed, 66 skipped` (was 3029 —
11 new tests); `make coverage` all floors met; `make lint`, `ruff format
--check`, `mypy --strict` (455 files), `make lint-imports` (2 contracts kept),
`make frontmatter`, `make validate-config`, `make scripts-coverage` (96 %,
unchanged), `make bridge-coverage` and `make contracts-coverage` (both 100 %)
all green.

Two deviations from this plan as written, both recorded in the milestones
below rather than quietly absorbed:

1. **M1.1's stated failing test was false.** The full-path ledger entry would
   have gone straight to green. The real guard is a new bare-basename check.
2. **M1.3's "no fallout" was wrong.** It reddened the corpus-count guard, which
   is the coupling the peer review predicted.

Scope also grew by one defect (`docs/workflow/graphs.md`), found only because
the live-doc denominator was widened from a hand-typed list to a derived one.

**PR 2 and PR 3 not started.**
- **Relationship to the plan of record:**
  [`20260916T214636Z-reliability-evidence-plan.md`](20260916T214636Z-reliability-evidence-plan.md)
  is unchanged and remains the spine. **PR 2 below *is* its PR F** — pass 1
  re-derived it without citing it, under a worse design. This plan does not
  restate it; it supplies the empirical validation PR F never had, and defers
  to it on everything else.

## Executive summary

Three PRs, in dependency order, none of which is the corpus pass 1 proposed.

**PR 1** fixes two live defects found while reviewing pass 1 — a stale module
path and a nine-family-stale lint claim, both in
`.github/copilot-instructions.md`, both passing all 3029 tests today — and
repairs the four guards that failed to catch them. It depends on nothing and is
worth landing whatever happens to the rest.

**PR 2** is the plan of record's PR F: split generic content into a root
`AGENTS.md`, keep Claude-specific surfaces in `CLAUDE.md` with `@AGENTS.md` as
its first line. Probe 10 confirms all three layers load. This is the entire
vendor-neutral benefit pass 1 was chasing, at roughly an afternoon.

**PR 3** delivers per-directory agent documentation with Mermaid maps and
sub-agent ownership tables — the original request — for **three** new
directories rather than sixty, each carrying one mechanically-checkable
semantic claim, and named **`CLAUDE.md`** because probes 3 and 9 show
`AGENTS.md` cannot be read in a tree that keeps a root `CLAUDE.md`. It extends
the convention this repo already has (`src/mangomas/core/CLAUDE.md`,
`tests/CLAUDE.md`) rather than founding a new one.

The constraint that shaped the sequencing is the repository's own, quoted in
the plan of record's Deferred section: *"adding to it before C4 measures it
increases maintenance without evidence."* PR 3 is therefore a **staged tranche
with an expansion gate**, not a corpus.

---

## What the request asked for, and what is delivered

The request was per-directory agent files, with Mermaid, naming sub-agents.
Measurement changed two of the four parameters. Stated plainly so the
substitution is visible rather than silent:

| Asked | Delivered | Why |
|---|---|---|
| Per-directory agent docs | **Yes**, PR 3 | — |
| Mermaid diagram in each | **Yes**, with a semantic check modelled on `test_architecture_docs.py` | Pass 1's "fence closed, ≤12 nodes" was weaker than the check the 6 existing diagrams already get |
| Sub-agents named per directory | **Yes**, `## Owners` table | — |
| Named `AGENTS.md` | **`CLAUDE.md`** | Probes 3 + 9: any root `CLAUDE.md`, *including a one-line `@AGENTS.md` pointer*, disables nested `AGENTS.md` entirely |
| In **every** directory (60) | **3 now**, expansion gated on measurement | The repo refuses unmeasured corpus growth in writing; and pass 1's bar admitted directories already covered one level up |

The vendor-neutral `AGENTS.md` the request implied is still delivered — at the
**root**, by PR 2, where it demonstrably works and where 30+ non-Claude agents
actually read it.

**If you want the full 60 anyway**, the honest route is: land PR 1 + PR 2,
land PR 3's three files, run the expansion gate (§ PR 3, M3.4), and expand on
its evidence. Skipping the gate means shipping 60 documents that nothing
measures and four guards actively mis-serve — which is the thing review
rejected, not the file count.

---

## PR 1 — live defects and the guards that missed them

Depends on nothing. Parallel-safe with everything. **Land first**: it proves
the guard argument on real specimens instead of asserting it.

### M1.1 — the live defects ✅

- **Correction to this plan, made during implementation.** The milestone
  originally read: *"add `src/mangomas/config.py` to `_VANISHED_PATHS`; it goes
  red immediately."* **That was false.**
  `test_live_docs_do_not_cite_vanished_module_files` substring-matches the
  **full path**, and no live doc cites it — verified by grep before writing any
  code. The edit goes straight to green and proves nothing. That is the same
  vacuous-proof failure this plan criticises in pass 1's mutation block, and it
  is recorded here rather than silently replaced.
- **Failing test first (actual):**
  `test_live_docs_do_not_cite_a_vanished_basename` — a new guard on the
  citation shape docs really use, the **bare basename**. Went red on two real
  defects on first run, naming `doc:line` and the remedy.
- **Depends on:** nothing.
- **Why basename existence is the wrong check:** `tests/constants/config.py`
  exists, so "does a file with this name exist anywhere?" **passes** on the
  very citation that is wrong. Vanished-ness is the signal, not resolvability.
- Defects fixed: `.github/copilot-instructions.md:27` (`config.py` →
  `config/`); `docs/workflow/graphs.md:154`, found only once the denominator
  widened (bare `composition.py`, plus `api/app.py::_ERROR_STATUS` → the
  canonical `api/errors.py::_ERROR_STATUS`); `CLAUDE.md`'s "in CI's order"
  claim, which is wrong in both directions.
- `.github/copilot-instructions.md:14` stated ruff's `select` as eleven
  families against a real twenty. **Fixed by deleting the restatement, not by
  re-syncing it** — a hand-copied mirror of a machine-readable table drifts the
  moment the table moves, which is exactly how this defect was born. The line
  now points at `[tool.ruff.lint]`.
- **Scope grew, honestly:** four narrative citations
  (`mango-layering-auditor.md`, `mango-decompose/SKILL.md` ×3) are legitimate —
  they say "that file no longer exists" / "the former `composition.py`
  module" — and are carried as (doc, module)-scoped `narrative_exemptions`,
  each of which must stay *earned* (below).

### M1.2 — the reverse-direction defaults guard ✅

- **Failing test first:** `tests/deploy/test_env_example_contract.py::
  test_every_settings_default_is_documented_somewhere` — the reverse of
  `_documented_defaults()`, with an explicit `_DEFAULT_UNDOCUMENTED_OK`
  frozenset mirroring `_CLAUDE_MD_UNDOCUMENTED_OK` (`:176`). Prove it red by
  deleting one default cell from `CLAUDE.md`, then restore.
- **Depends on:** nothing.
- **Why this is the most valuable milestone in the plan.** Pass 1 asserted that
  file binds CLAUDE.md's config table to `Settings` "in both directions,
  including documented defaults". Review measured it: the **names** direction
  regexes `\bMANGOMAS_[A-Z0-9_]+\b` over the *whole file*, not the tables
  (`:91-94`), and the **defaults** check is one direction with a floor of one
  row (`:323-351`) — there is no `model - documented` assertion anywhere.
  A trim could delete 95 of ~96 default cells and stay green. That is this
  repository's signature defect class, currently live in its best-guarded file.
- Correct the assertion message at `:190`, which says "config tables" for a
  whole-file check.

### M1.3 — repair the two corpus-constant gaps ✅

- **Failing test first:** add `mango-harness-dev` to
  `PROTECTED_PATH_OWNER_SLUGS` (`tests/constants/corpus.py`). It declares
  ownership of four protected paths and was absent, so the roster the trailer
  test walks was a strict subset of the agents that need the trailer.
- **Depends on:** nothing.
- **Correction: "one line, no fallout" was wrong.** The edit turned
  `test_prose_corpus_counts_match_the_live_corpus` red —
  `CLAUDE.md` said *"Four agents own a protected path"* and the tree now has
  five. The count guard did its job; the prose was updated with it. This is the
  `LIVE_CORPUS_COUNT_DOCS` coupling the peer review flagged (F13/N5), hit in
  practice on the first milestone that touched a roster.
- While in that prose, added the **trailer spelling rule** to `CLAUDE.md`:
  path bare and unbackticked as the first token, one trailer per path. G3
  measured that a backticked path silently degrades a scoped marker into a
  blanket approval, and every path in these documents is backticked.
- Correct the stale governance claim at `tests/constants/corpus.py:645-647`
  ("nothing here stops a `Bash` heredoc or `>` redirect"): redirections and
  recognised Bash file commands **are** covered by the deny rule today. The
  residual gap is an arbitrary subprocess (a Python one-liner), which is what
  the comment should say.

### M1.4 — extend the path guard to inline backticks ✅

- **Failing test first:** extend `tests/tooling/test_doc_links.py` with a
  backtick scanner. `_MD_LINK_RE` (`:50`) matches only `[text](target)`, so
  inline `` `path` `` references are unchecked. Prove it red against a fixture
  containing `` `src/mangomas/does_not_exist.py` ``.
- **Depends on:** M1.1 (which fixes the one live violation, so the new check
  lands green).
- **Must resolve bare basenames, not just slash-bearing paths.** Probed: a
  slash-requiring regex returns `[]` on `config.py` — it would have missed
  M1.1's defect entirely. Resolve a bare `foo.py` by searching the tree for that
  basename; where it is ambiguous, require the check to say so rather than
  guess.
- Written as an extension it inherits the non-vacuity guard at `:69-80` free.

---

## PR 2 — root split (= plan of record PR F)

**This PR is not a new design.** `20260916T214636Z-reliability-evidence-plan.md`
`:284-296` specifies it, names its failing test, and records
`Depends on: nothing` and *"highest value-per-effort item in this plan: roughly
an afternoon"*. Implement it as written. This plan's only additions are the
validation below and one correction.

### M2.1 — implement PR F

- **Failing test first:** `tests/test_agents_md_contract.py::
  test_claude_md_first_line_imports_agents_md`, as PR F already names it.
- **Depends on:** nothing (PR 1 is parallel-safe).
- Move build/test/lint/convention content to a root `AGENTS.md`; keep
  Claude-specific surfaces (skills, hooks, agent corpus, `@imports`) in
  `CLAUDE.md` with `@AGENTS.md` as its first line.

### M2.2 — the validation PR F never had

- **Failing test first:** extend M2.1's test to assert the import **resolves**,
  not merely that the line is present — read `AGENTS.md` and assert a sentinel
  heading from it. A first line that points at a missing file is exactly the
  silent failure this repo keeps finding.
- **Depends on:** M2.1.
- Record probe 10 in `ADR-0035`: `CLAUDE.md` carrying `@AGENTS.md` **plus**
  Claude-specific content, a generic root `AGENTS.md`, and a nested
  `solo/CLAUDE.md` — **all three load**. Measured on 2.1.278, Linux.
- Record the negative results too, because they are what constrain PR 3: probes
  3 and 9 show any root `CLAUDE.md` disables nested `AGENTS.md`; probes 5–8
  show `claude-md-and-agents-md` is honoured **only from `~/.claude/settings.json`**
  and is inert in project settings, so it can never be a committed mechanism.
- State the expiry: `instructionFiles` semantics are eight days old. Re-run the
  matrix before merge; record a measurement with a date, not a law.

### M2.3 — repoint the contracts

- **Failing test first:** the four tests reading `_CLAUDE_MD`
  (`tests/deploy/test_env_example_contract.py:30`) go red the moment content
  moves; that is the signal, and M1.2's reverse guard is what makes the move
  safe.
- **Depends on:** M1.2 (**hard** — do not move a config row before the reverse
  direction exists), M2.1.
- `CORPUS_DOC_RELPATHS` (`tests/constants/corpus.py:207-220`) asserts each
  listed file exists, so leaving `"CLAUDE.md"` in the tuple keeps the test green
  while policing a stub. **Repoint means *add* `AGENTS.md`**, not keep
  `CLAUDE.md` alone. Same for `_LIVE_FILES` in `test_live_path_ledger.py`.
- `pyproject.toml:197,206,265` and `src/mangomas/harness/governance.py:40`
  mention `CLAUDE.md` **in comments only**, and nothing scans them. Under PR F
  `CLAUDE.md` still exists, so those comments are not even stale. **Leave them
  alone in this PR.** Pass 1 presented editing them as forced and accepted two
  protected-path trailers for it; that was a choice dressed as a constraint.
- If a later PR does touch them, the trailer rule is exact: **bare, unbackticked
  path as the first token after the colon, one trailer per path.**
  `find_marker_scopes` (`scripts/_governance.py:162-176`) scopes a marker only
  on an exact match; a backticked path, a typo, or a comma-joined list falls
  through to `has_unscoped`, which approves **every** touched protected path
  (`:126`). Every path in this document is backticked — do not copy that style
  into a commit message.

---

## PR 3 — per-directory files, three of them

Delivers the original request under the measured constraints. Depends on PR 2
for the root split, and on PR 1 for the guards it reuses.

### M3.0 — extract the corpus helpers

- **Failing test first:** none — pure refactor, green at every commit. The
  proof is that `tests/tooling/test_corpus_contract.py` passes unchanged
  afterwards.
- **Depends on:** nothing.
- Pass 1 claimed the new suite would "reuse
  `test_agent_skill_owners_resolve_to_a_real_skill`'s helpers". That test has
  none — it is a set difference over a constant. The real helpers are
  module-private, and `_agent_paths()` shells out to `git ls-files` on **every**
  call (`:190-196`).
- Extract `_skill_dirs` / `_agent_paths` / `_agent_frontmatter` / `_agent_body`
  into `tests/tooling/_corpus.py`, `@functools.cache` the `git ls-files` call,
  repoint the existing suite.

### M3.1 — the three files

- **Failing test first:** `tests/tooling/test_directory_claude_md.py::
  test_inventory_matches_the_tree` — set equality against a declared inventory,
  mirroring `EXPECTED_AGENT_SLUGS`' style so editing the tuple *is* the review
  record. Red until the files exist.
- **Depends on:** M3.0, PR 2.
- **Named `CLAUDE.md`, not `AGENTS.md`** (probes 3, 9). This extends the
  existing convention; it founds nothing. Add each to `CORPUS_DOC_RELPATHS`, as
  the two survivors already are (`:218-219`).
- **The three, and the checkable claim each carries** — the entry bar is *one
  mechanically-verifiable semantic claim per file*, not "a directory exists":

  | Directory | Local rule worth stating | Its checkable claim |
  |---|---|---|
  | `src/mangomas/composition/` | single wiring point; all registration happens here | every name the file lists as registered resolves in the matching registry |
  | `src/mangomas/api/` | middleware install order is load-bearing (backpressure inner of log/trace) | the documented order equals `create_app`'s actual install order, read by AST |
  | `src/mangomas/adapters/` | protocol-first; heavy SDKs lazy-imported behind extras | every subdirectory listed has a `base.py` declaring a `@runtime_checkable` Protocol |

- **Not included, with reasons** (each already covered one level up — pass 1's
  own bar, applied honestly): `utils/` is already exempted by
  `test_architecture_docs.py:57` with the recorded reason *"shared utilities
  without architectural boundary"*; `core/orchestrator/`'s protected status is
  already stated in `src/mangomas/core/CLAUDE.md:12`; `workflow/predicate/`'s
  never-raises contract is already in `_client.py`'s module docstring;
  `tests/constants/`'s re-export rule is already in `tests/CLAUDE.md:60-64`;
  `eval/{scorers,sinks,sources,targets}/` would be four near-identical Owners
  tables under one agent and one skill; `examples/` is two JSON files covered
  by `docs/workflow/graphs.md`.

### M3.2 — content contract, and the rules it must not break

- **Failing test first:** `test_directory_claude_md_sections_are_canonical`,
  against a **new** constant `DIRECTORY_DOC_SECTION_HEADINGS`.
- **Depends on:** M3.1.
- Sections: `## Scope`, `## Map` (Mermaid), `## Owners` (agent + skill table),
  `## Invariants`, `## Boundaries`, `## Verify`. ≤150 lines, ≤50 per section,
  no frontmatter.
- **A second constant is mandatory, not a preference.**
  `AGENT_SECTION_HEADINGS` (`tests/constants/corpus.py:587-599`) has exactly
  nine members and **`## Owners` is not among them**; five of the six above are
  absent. Pass 1 proposed extending that frozenset — which would let
  `.claude/agents/mango-*.md` carry `## Map` or `## Verify` and still pass
  `test_agent_headings_use_the_canonical_vocabulary`, defeating the guard whose
  own comment pins it at *"Nine, not seven"* precisely because *"an ad-hoc name
  is where a duplicated section hides"*.
- **Link mechanised rules; never restate them.** Pass 1 instructed each
  `Boundaries` section to state the protected-path obligation *verbatim* and to
  repeat the import-linter independence contract in four files. Both are wrong:
  `test_protected_path_governance_is_single_sourced` (`:585-593`) exists
  because that prose *"was byte-identical across four agents"* and asserts
  exactly one carrier — and its glob is `.claude/**/*.md`, so a corpus under
  `src/` would reintroduce the duplication **where the guard cannot see it**.
  The independence contract is mechanised at `pyproject.toml:388-390` and runs
  in `make gate`; restating it in prose is spec-0022 R15 run backwards.
- **Correct protected-path map** — pass 1's B0 said "`composition/`, `api/`,
  `adapters/`, `core/orchestrator/` … four being protected paths". Against
  `pyproject.toml:136-154`, **none** of the first three contains a protected
  file. Directories that do: repo root, `scripts/`, `src/mangomas/`,
  `src/mangomas/core/`, `src/mangomas/core/orchestrator/`,
  `src/mangomas/harness/`. Two of PR 3's three files must therefore state
  *no* protected-path obligation — and `src/mangomas/harness/` is a protected
  path with **no** flag, so it is not a "default-off subsystem" either.

### M3.3 — the guards, with the claim taxonomy stated honestly

- **Failing test first:** one case per check in
  `tests/tooling/test_directory_claude_md.py`, each fed a **fixture** violating
  exactly that rule. Fixtures, not the live tree — pass 1's mutation proof ran
  `replace("## Boundaries", "## Notes")` against a root file that contains
  **zero** `## Boundaries` headings, so `str.replace` returned the string
  unchanged, the file was rewritten byte-identically, and the gate exited 0. A
  proof that cannot fail is the thing `mango-mutation-proof` exists to prevent.
- **Depends on:** M3.2, M1.4 (reuses the basename resolver).

  | Class | Check | Verdict |
  |---|---|---|
  | Paths | resolve, incl. bare basenames (M1.4) | mechanical |
  | Symbols | `` `Foo.bar` `` → find `class Foo` under `src/mangomas/**`, require `def bar`/`async def bar` | mechanical; `import ast` is an established idiom here (8 test modules already use it) |
  | `## Verify` commands | `make X` → target exists (reuse `_make_target_body`, `test_ci_make_parity.py:75-82`); `pytest` → path args exist; `mangomas X` → in `EXPECTED_CLI_COMMANDS` | mechanical |
  | Agents / skills | `mango-*` resolves to a real file | mechanical |
  | Mermaid | every node/edge names a module that exists in that directory — modelled on `test_architecture_docs.py:11-18`, **not** "fence closed, ≤12 nodes" | mechanical |
  | Sections, length, no frontmatter | structural | mechanical |
  | Restating a mechanised rule | forbid the strings that mark it (e.g. `advisory only`, `text/event-stream`) | mechanical, as a **prohibition** — far cheaper than a parity check |
  | **Rationale, "why", boundary prose** | — | **not checkable. Stated as the residue in ADR-0035, and the reason the tranche is three files and not sixty.** |

- The symbol check is what pass 1 lacked. `TurnRepository.save()` — the
  fictional method that helped retire the `agent.md` corpus — lives on a **real
  class in a real file** (`src/mangomas/adapters/storage/base.py:17`, methods
  `save_turn`/`list_turns`/`close`). Path existence returns green on it. The
  ten-line basename-plus-`def` version catches it on the day it is written.

### M3.4 — wire the gate, and the expansion gate

- **Failing test first:** `test_gate_includes_directory_docs` beside the
  existing `test_gate_includes_lint_imports` (`test_ci_make_parity.py:385`).
  Nothing else binds `gate`: `test_lint_job_delegates_every_step_to_make`
  (`:88-97`) asserts a **hard-coded literal list** and reads `ci.yml` only —
  it never opens the `Makefile`. **Three artefacts move together**
  (`ci.yml`, that literal, `Makefile:146`) and pass 1 named two.
- **Depends on:** M3.3.
- Prefer keeping this in **pytest**, not a new `scripts/` entry point. Measured:
  `scripts/` runs at **96.28 %** against a floor of 94 pinned by
  `test_isolated_coverage_floors_are_pinned` (`:375`), leaving **≤18 uncovered
  units** — less than any real linter. If a script is added anyway it must go
  into `SCRIPTS_TESTS` (`Makefile:24-28`, a hand-enumerated list that does
  **not** include `tests/tooling`) in the same commit, join `.PHONY`
  (`:67-71`), and pass `mypy --strict` (`CODE_PATHS` includes `scripts`).
  **Do not** import `tests/constants/corpus.py` from a script: measured, that
  pulls **233 modules** and fails without `pydantic`, and a `try/except`
  fallback means CI exercises the heavy path while the stdlib path is never
  proven. If an inventory must be shared, put it in a standalone
  `tomllib`-readable file and re-export it into `corpus.py`.
- Forbid `AGENTS.md` under `.claude/agents/` or `.claude/skills/` as a lint
  rule. `AGENTS_GLOB` is `.claude/agents/**/*.md` — **any** `.md` — so a file
  there turns the frontmatter lint red (mutation-proved: *"missing frontmatter
  (expected leading ---)"*, exit 1). Loud, but it should be a stated rule, not
  an inventory row.
- **Expansion gate.** Before any fourth file, record in `ADR-0035` whether the
  three changed anything: pick two recurring task types per covered directory,
  run them with and without the file, and report. If nothing is measurable, the
  tranche stays at three and the corpus stays closed — which is the plan of
  record's rule (*"adding to it before C4 measures it increases maintenance
  without evidence"*) applied to documents instead of agents.

---

## Deferred / out of scope

- **The 60-file corpus.** Withdrawn, not merely postponed. Re-opening requires
  M3.4's expansion evidence **and** a design that survives probe 9 — which, as
  long as the repo keeps a root `CLAUDE.md`, means the files are named
  `CLAUDE.md`.
- **Deleting the root `CLAUDE.md` so nested `AGENTS.md` works** (probe 4 shows
  it would). Rejected: it forfeits `@imports`, hooks and the Claude-specific
  surfaces PR F deliberately keeps, to gain a filename.
- **Committing `instructionFiles: claude-md-and-agents-md`.** Impossible, not
  merely unwise — probes 5 and 6 show project settings ignore it. Contributor
  convenience only, never a project mechanism.
- **Editing `.claude/settings.json` at all.** Not needed by anything above.
  Note for whoever eventually does: the `ConfigChange` hook's default mode is
  `off` and returns `allow` immediately
  (`src/mangomas/harness/config_audit.py:56-57`) — it runs but decides nothing,
  so "expect it to fire" is wrong; and in `block` mode it would block the
  human's own edit, since it fires on the file changing, not on who changed it.
- **An ADR reversing the `agent.md` retirement.** Withdrawn. Nothing that
  survives reverses it, and `RETIRED_STRAY_AGENT_FILENAME` stays untouched.
- **Third-party linters** (`agents-lint`, AgentLinter). Evaluated, not adopted:
  every check worth having here is repo-specific. Re-proposing one means naming
  a check it makes that M3.3's taxonomy does not.
- **Widening `test_protected_path_governance_is_single_sourced`'s
  `.claude/**/*.md` glob to the whole repo.** A cheap, independent improvement
  worth taking on its own, and a precondition if the corpus ever grows past
  PR 3 — but not this plan's to carry.

## Verification

```bash
make gate                 # full pre-PR chain (NB: not CI's order — M1.1 fixes that claim)
python -m pytest tests/tooling tests/deploy -q   # corpus, doc-governance and env-contract suites
make frontmatter          # unchanged — proves the two corpora stayed separate
make scripts-coverage     # only if M3.4 adds a script; headroom is ~18 units
```

Baseline at `115017c`, for regression comparison: **3029 passed, 66 skipped**
(48.38 s); `tests/tooling tests/deploy` = **597 passed, 1 skipped**.

Per-PR mutation proof (`.claude/skills/mango-mutation-proof/SKILL.md`), run
against a **fixture** and scripted in one block so the restore cannot be skipped:

```bash
python - <<'PY'
import pathlib, shutil, subprocess, sys, tempfile
src = pathlib.Path("tests/tooling/fixtures/directory_doc_valid.md")
with tempfile.TemporaryDirectory() as d:
    bak = pathlib.Path(d) / "bak.md"; shutil.copy2(src, bak)
    try:
        src.write_text(src.read_text().replace("## Boundaries", "## Notes", 1))
        assert "## Notes" in src.read_text(), "mutation did not apply — proof is vacuous"
        red = subprocess.run([sys.executable, "-m", "pytest", "-q",
                              "tests/tooling/test_directory_claude_md.py"]).returncode
    finally:
        shutil.copy2(bak, src)
    green = subprocess.run([sys.executable, "-m", "pytest", "-q",
                            "tests/tooling/test_directory_claude_md.py"]).returncode
print(f"mutated={red} (MUST be non-zero)   restored={green} (MUST be 0)")
raise SystemExit(0 if red != 0 and green == 0 else 1)
PY
```

The `assert` after the mutation is the correction that matters: pass 1's proof
used `str.replace` on a heading the target file did not contain, so it rewrote
the file byte-identically and asserted a gate failure that could never happen.
