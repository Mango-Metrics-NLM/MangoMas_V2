# AGENTS.md corpus — peer review, source-verified and probed (2026-09-19)

- **Scope:** `Mango-Metrics-NLM/MangoMas_V2` @ `115017c` (branch
  `claude/agent-md-documentation-u08et8`), package `mangomas`.
- **Method:** full local checkout, `.venv` created, `pip install -e ".[dev]"` +
  `-e ./mango-integration-contracts` executed. **Suite executed:**
  `python -m pytest -q --no-cov` = **3029 passed, 66 skipped** in 48.38 s at
  `115017c`; doc-governance subset `tests/tooling tests/deploy` = **597 passed,
  1 skipped**. Instruction-file loading was **probed against the installed
  `claude` binary (2.1.278)** across **ten fixture trees**, not read off a blog.
  The path-regex claim was probed with a script. Three specialist reviews
  (`mango-architect`, `mango-test-engineer`, `mango-harness-dev`) ran in
  parallel against the same commit. Every verdict cites a file, a line, or
  command output.
- **Subject:** [`docs/plans/20260919T213539Z-agents-md-corpus-plan.md`](../plans/20260919T213539Z-agents-md-corpus-plan.md)
  — a plan to add 60 per-directory `AGENTS.md` files with Mermaid maps and
  sub-agent ownership tables.
- **Relationship to prior work:** does not supersede
  [`20260919-council-rejection-and-replan.md`](20260919-council-rejection-and-replan.md);
  it adopts its method (a finding is admitted only with a receipt) and applies
  it to a plan written after it. It **does** correct the subject plan's failure
  to cite
  [`20260916T214636Z-reliability-evidence-plan.md`](../plans/20260916T214636Z-reliability-evidence-plan.md),
  which is the plan of record (§1, F9).

---

## 0. Verdict

**Reject the corpus. Keep four things out of it.**

The plan's central design is **measured not to work**. Probe 9 runs D1's exact
proposal — `CLAUDE.md` containing `@AGENTS.md`, a root `AGENTS.md`, a nested
`solo/AGENTS.md` — and the nested file **does not load**. Every one of the 60
files the plan proposes would have been invisible to Claude Code. The plan
would have reproduced, at sixty times the scale, the precise defect it opens by
quoting: *"a file nothing loads cannot be kept honest."*

Independently, the root half of the plan **already exists as approved work**
under a cheaper design (F9), and the repository has **already refused** corpus
growth without measurement, in writing, in that same plan (F10).

Four things survive and should be extracted: the live-defect fixes (F3, F4),
the guard-strengthening work (F11), the root split as already planned (F9), and
A0 itself — which was good instinct and is now executed (F1).

---

## 1. Findings

Prefix key: **F** = this review's own probes and reads · **N** = `mango-architect`
· **T** = `mango-test-engineer`. Specialist findings are restated with their
citations and independently spot-checked where marked.

### F1 — Instruction-file loading, measured · **The plan's design is refuted**

`claude --version` → **2.1.278** in this container, one patch above the 2.1.277
that added `AGENTS.md` support. Ten `git init` fixture trees, canary tokens in
each instruction file, queried with `claude -p`:

| # | Root file(s) | Nested file | `instructionFiles` | Nested loaded? |
|---|---|---|---|---|
| 1 | `CLAUDE.md` | — (eager test, no tools) | default | **nothing nested loads eagerly** |
| 2 | `CLAUDE.md` | `sub/CLAUDE.md` + `sub/AGENTS.md` | default | `CLAUDE.md` ✓, `AGENTS.md` ✗ |
| 3 | `CLAUDE.md` | `solo/AGENTS.md` only | default | **✗** |
| 4 | `AGENTS.md`, no `CLAUDE.md` | `solo/AGENTS.md` | default | ✓ |
| 5 | `CLAUDE.md` | `solo/AGENTS.md` | **project** `.claude/settings.local.json` | ✗ — setting inert |
| 6 | `CLAUDE.md` + `AGENTS.md` | — | **project** settings | root `AGENTS.md` ✗ — inert |
| 7 | `CLAUDE.md` + `AGENTS.md` | — | **user** `~/.claude/settings.json` | root `AGENTS.md` ✓ |
| 8 | `CLAUDE.md` | `solo/AGENTS.md` | **user** settings | ✓ |
| **9** | **`CLAUDE.md` = `@AGENTS.md`** + `AGENTS.md` | **`solo/AGENTS.md`** | **default** | root ✓, **nested ✗** |
| **10** | **`CLAUDE.md` = `@AGENTS.md` + content** + `AGENTS.md` | **`solo/CLAUDE.md`** | **default** | **all three ✓** |

Five conclusions, each load-bearing:

1. **Nested files are always lazy** (probe 1). A 60-file corpus at the measured
   ~1.1 k tokens/file would be ~66 k tokens if eager. It is not — cost is one
   file per subtree touched. *The affordability half of the design was sound.*
2. **Any root `CLAUDE.md` disables the whole `AGENTS.md` path, root and
   nested** (probes 3, 4 control).
3. **A one-line `@AGENTS.md` pointer does not escape this** (probe 9). The
   import resolves at root — `CANARY_IMPORTED_6T1` loads — but the nested
   `AGENTS.md` still does not. **This is the finding that refutes the plan.**
   D1 was written believing the pointer bought both worlds; it buys one.
4. **§ D2's mitigation cannot be committed.** `claude-md-and-agents-md` is
   honoured from `~/.claude/settings.json` (probes 7, 8) and **ignored** from
   project settings (probes 5, 6). Every contributor would have to set it on
   their own machine, unverifiably.
5. **The working design is probe 10:** `CLAUDE.md` carrying `@AGENTS.md` *plus*
   Claude-specific content, a generic root `AGENTS.md`, and per-directory files
   named **`CLAUDE.md`**. All three layers load. This is exactly the plan of
   record's PR F (F9) plus the convention this repo already has.

So the per-directory corpus, if built at all, **must be named `CLAUDE.md`**.
The repository's existing choice was correct and the plan's central proposal
was a regression dressed as a standards upgrade.

*(The temporary `~/.claude/settings.json` written for probes 7–8 was removed.)*

### F2 / N1 / T1 — The headline lint check cannot catch the recorded failure · **Refuted**

§ D4 ranks path existence as *"the single highest-value check … the mechanical
answer to the hallucinated paths that killed the `agent.md` corpus."*

`tests/tooling/test_corpus_contract.py:616-621` names the two defects verbatim:
*"a fictional `TurnRepository.save()`, an SSE format a client could not
parse."* Neither is a path.

`src/mangomas/adapters/storage/base.py:17` declares `class
TurnRepository(Protocol)` with `save_turn` (`:20`), `list_turns` (`:29`),
`close` (`:35`). **`save` does not exist.** The file is real, the class is real,
only the method is fictional — path existence returns green. The SSE defect
contains no path at all; the repo's actual fix was a behaviour test
(`tests/test_streaming.py:182`).

D4's seven checks are inventory equality, path existence, `mango-*` name
resolution, section structure, Mermaid structure, exemption listing, ledger
extension. **None binds a claim to a behaviour.**

Worse, the check misses a real path too. Probed:

```
naive (slash-required) hits on config.py: []
bare-filename hits: ['config.py', 'errors.py', 'test_errors.py']
```

The live drift in this tree (F3) is the bare token `` `config.py` ``; a
"repo-relative path" regex requires a `/` and returns nothing.

**T3's remedy, adopted:** AST symbol resolution is both checkable and an
established in-repo idiom — eight test modules already `import ast`
(`tests/test_import_compat.py:24`, `tests/cognitive/test_cognitive_layering.py:5`,
`tests/mango_contracts/test_layering.py:5`, and five more). A no-AST 80 %
version costs ten lines: for each `` `Foo.bar` ``, find `class Foo` under
`src/mangomas/**/*.py` and require `def bar`/`async def bar` in that file. That
alone catches `TurnRepository.save()` on the day it is written.

**T3(b), the largest unguarded surface:** D3 mandates a `## Verify` bash block
and D4 never checks it. The plan proposes authoring 60 commands with zero
verification, which is anti-pattern 2 ("wrong commands") as the plan itself
defines it.

### F3 — Live stale path in the tree, invisible to 3029 tests · **Confirmed — defect**

`.github/copilot-instructions.md:27` names `` `config.py` ``.
`src/mangomas/config.py` does not exist: deleted in `e2c177b`
*("refactor(config): decompose config.py into a domain package behind a
permanent facade")* — the same ADR-0019 wave that produced `composition.py` and
`api/middleware.py`.

`tests/tooling/test_live_path_ledger.py` exists to catch exactly this, and its
`_VANISHED_PATHS` names **two of the three**:

```python
_VANISHED_PATHS: tuple[str, ...] = (
    "src/mangomas/composition.py",
    "src/mangomas/api/middleware.py",
)
```

`.github/copilot-instructions.md` **is** in that test's `_LIVE_FILES` (`:24`),
so the file is scanned — the ledger entry is simply missing. Full suite green
with the stale reference live.

**Fix:** add `"src/mangomas/config.py"` to `_VANISHED_PATHS`; correct line 27 to
name `config/`. One line each.

### F4 — Second live drift, of the unguardable kind · **Confirmed — defect**

`.github/copilot-instructions.md:14` states ruff's configuration as eleven
families (`E,F,I,B,UP,SIM,PL,RUF,S,SLF,ARG`). `pyproject.toml`'s
`[tool.ruff.lint]` selects **twenty** — that eleven plus `LOG, G, ASYNC, ERA,
DTZ, TID, C4, PTH, T20`, adopted as the spec-0020 R4 ratchet. Stale by nine
families, with no path involved, so no path check at any sophistication catches
it.

The only mechanism that holds is the one this repo already invented:
`tests/deploy/test_env_example_contract.py` asserts prose against live config
because prose mirroring machine-readable config must be *asserted*, not written.

### F5 / N4 — A three-file problem treated as two · **Confirmed / Corrected**

§ D1 frames the decision as `CLAUDE.md` vs `AGENTS.md`. There are three root
agent-instruction surfaces:

| File | Lines | Audience | Claim guards | Drift found |
|---|---|---|---|---|
| `CLAUDE.md` | 710 | Claude Code | config names + defaults, corpus counts, doc links, path ledger | **none** |
| `.github/copilot-instructions.md` | 118 | Copilot | retired-path + vanished-module only | **two** (F3, F4) |
| `.claude/skills/*/SKILL.md` ×20 | — | both | frontmatter lint, corpus contract | none |

N4 corrects the plan's benefit claim: D1 rejects "keep `CLAUDE.md` only" as
*"forfeits the vendor-neutral standard for Codex/Cursor/Copilot"*, but
`.github/copilot-instructions.md` already serves Copilot and is a governed
corpus doc (`tests/constants/corpus.py:211`, `test_live_path_ledger.py:24`).
The honest residual benefit is **Cursor and Codex only** — both real here
(`docs/analysis/20260908-…-architecture-review.md:6` records a
`cursor/cognitive-contracts-1036` checkout; `.gitignore:61` ignores `.cursor/`)
but narrow, and **none of it requires 60 nested files**.

### F6 — Scope is unargued; the evidence points at a different variable · **Confirmed**

Guard coverage, not length, predicts honesty: the 710-line file that violates
every "under 150 lines" guideline in the plan's own research section is the
clean one (≈4 guard surfaces); the 118-line file that respects it carries both
drifts (≈0 claim-level guards).

**N7 names specific directories that fail the plan's own bar** — *"would an
agent working here be wrong without it?"* — each already covered:

- `src/mangomas/utils/` — one 13-line module, no `__init__.py`; the repo has
  **already adjudicated it**, `tests/tooling/test_architecture_docs.py:57`
  exempts `utils` with the recorded reason *"shared utilities without
  architectural boundary"*. The plan lists it among "four thinnest leaves that
  still earn a file" and gives **three rationales for four directories** —
  `utils` gets none.
- `src/mangomas/core/orchestrator/` — rationale "protected-path status" is
  already stated one directory up, in `src/mangomas/core/CLAUDE.md:12`, the very
  file the plan calls its model.
- `src/mangomas/workflow/predicate/` — "never-raises closure contract" already
  in `_client.py`'s module docstring (`:4-9`) and `compile_predicate`'s own.
- `src/mangomas/eval/{scorers,sinks,sources,targets}/` — four sibling registry
  dirs, one owning agent, one owning skill, four near-identical Owners tables.
- `tests/constants/` — C3 says its file must state the `X as X` re-export
  contract; `tests/CLAUDE.md:60-64` already states exactly that, in the file
  Claude Code auto-loads for the whole subtree.
- `examples/` — two JSON files, already covered by `docs/workflow/graphs.md`.

### F7 / T11 / T12 — Gate wiring is wrong in three places · **Refuted**

**T11 — A4 breaks `make scripts-coverage`.** `SCRIPTS_TESTS` (`Makefile:24-28`)
is a **hand-enumerated list, not a glob**, and `tests/tooling` is not in it. The
plan puts the new script's tests at `tests/tooling/test_lint_agents_md.py`, so
they would never run, while `--source=$(SCRIPTS_SRC)` (`Makefile:142`) measures
all of `scripts/` including the new unexercised file. With `scripts/` at 96 %
against `SCRIPTS_FLOOR ?= 94` over ~1100 statements, the budget is ~22
uncovered statements; any real linter blows it. **The step is a one-line
`SCRIPTS_TESTS` edit in the same commit.** The plan says only "must still clear
`SCRIPTS_FLOOR`", which is a wish.

**T12 — the CI-parity claim is wrong, and a third artefact is unbound.**
`test_lint_job_delegates_every_step_to_make`
(`tests/deploy/test_ci_make_parity.py:85-97`) asserts a **hard-coded literal
list** and reads `ci.yml` only — it never opens the `Makefile`. So three
artefacts move together and **nothing binds the third**: A4 could update
`ci.yml` and the literal, forget `gate`, and stay green while `make gate` never
runs the lint. Needs `test_gate_includes_agents_md` beside the existing
`test_gate_includes_lint_imports` (`:385`). Also: `agents-md` must join
`.PHONY` (`Makefile:67-71`), untested and a silent no-op if omitted.

**T12(2), independently notable:** `make gate`'s order
(`… typecheck lint-imports frontmatter protected-paths …`) is **not** CI's order
(`… format-check, frontmatter, typecheck, lint-imports`). The plan's
"full pre-PR chain in CI's order" is wrong — **and so is the identical claim in
`CLAUDE.md`**. That is a live documentation defect of the same class as F3/F4,
found in the best-guarded file.

**F7(own) — pre-commit coupling is lighter than feared.**
`tests/tooling/test_precommit_parity.py` requires exactly three local hook IDs
and documents pre-commit as an explicit subset of `make gate`, so **no
pre-commit hook is required**. The frontmatter hook is scoped
`files: ^\.claude/(skills/.*/SKILL\.md|agents/mango-.*\.md)$`, so a stray
`AGENTS.md` cannot be captured by it — the plan's stated worry is unfounded.

### F8 / T5 — The C0 claim is half right, and the weaker half is the dangerous one · **Corrected**

The plan asserts `test_env_example_contract.py` binds CLAUDE.md's config tables
to `Settings` *"in both directions, including documented defaults compared
against live field values"*, and builds milestone C0 on it.

**Names direction: Confirmed.** Four tests read `_CLAUDE_MD` (`:30`); a rename
reddens all four; moving a config row reddens
`test_claude_md_documents_every_settings_field` (`:179-190`) — C0's proposed
failing test is real.

**Two corrections, both material:**

1. **Neither name direction is scoped to "the config tables."** `_names_in`
   (`:91-94`) regexes `\bMANGOMAS_[A-Z0-9_]+\b` over the **whole file**. A trim
   can delete every table, keep a flat list of names, and stay green. The
   assertion message at `:190` says "config tables"; that is inaccurate and the
   plan repeats it.
2. **The defaults check is one direction with a floor of one row.**
   `test_claude_md_documented_defaults_match_the_model` (`:323-351`) asserts
   non-vacuity (`assert documented`, floor **one**), documented ⊆ model, and
   value agreement *for names already documented*. **There is no
   `model - documented` assertion anywhere in the file.** A trim deleting 95 of
   ~96 default cells passes all three.

**Consequence:** C1's "Move, do not delete" could silently lose every default
cell — the repo's own signature defect class. **The reverse-direction test
(`test_every_settings_default_is_documented_somewhere`, with an explicit
`_DEFAULT_UNDOCUMENTED_OK` mirroring `_CLAUDE_MD_UNDOCUMENTED_OK` at `:176`)
must land in C0, not C1.** This is the single most valuable piece of
engineering the plan gestures at, and it is valuable whether or not a corpus is
ever built.

### F9 / N3 — The root half is already approved work, under a cheaper design · **Blocker**

`docs/plans/20260916T214636Z-reliability-evidence-plan.md:284-296` is
**PR F — `AGENTS.md` bridge (parallel-safe; land any time)**:

> Move build/test/lint/convention content from `CLAUDE.md` into a root
> `AGENTS.md`; keep Claude-specific surfaces (skills, hooks, agent corpus,
> `@imports`) in `CLAUDE.md` with `@AGENTS.md` as its first line.
> … **Highest value-per-effort item in this plan: roughly an afternoon**

It already names its failing test
(`test_claude_md_first_line_imports_agents_md`) and has `Depends on: nothing`.

Grepping the subject plan for `reliability|PR F|20260916|plan of record` returns
**no matches**. D1's options table lists four options; **PR F's split is not one
of them**. It is strictly better than `git mv` + a one-line pointer: Claude-
specific content stays where Claude Code's precedence expects it, so the root
file never changes identity wholesale.

**Probe 10 validates PR F empirically** — something the plan of record never
did. That is this review's one genuine contribution to the root question.

### F10 / N6 — The repo has already refused unmeasured corpus growth · **Blocker**

Same plan, Deferred / out of scope (`:302-305`):

> **A 28th agent, or any new workflow node kind.** The corpus has no routing
> eval; adding to it before C4 measures it increases maintenance without
> evidence. Re-opening requires C4 data showing the existing roster is
> saturated.

The subject plan adds **60 documents** to the same agent-facing corpus — vastly
more than "a 28th agent" — with no measurement, and cites neither the deferral,
nor milestone C4, nor the plan of record.

Two secondary corrections from N6, both accepted:

- **spec-0022 R15 is narrower than the plan's paraphrase.**
  `specs/0022-governance-hardening-adoptions.md:64` reads in full: *"R15:
  CLAUDE.md's Key Design Rules table gains an 'Enforced by' column."* That is a
  requirement to **admit** what is prose-only. The plan cites it as authority
  for *building the mechanism first*, which it does not say. The principle is
  real; the citation is loose.
- **The plan breaches the fetch-receipt rule it operates under.**
  `20260919-council-rejection-and-replan.md:452` (D14) recommends discarding any
  contribution lacking a URL/status/SHA receipt. The plan's § Standards research
  self-reports that key domains were egress-blocked and summarised from search
  results — then its executive summary presents the visibility finding as
  established fact. F1 now supplies the missing receipt, and it says the
  opposite of what the plan assumed.

### F11 / N8 / N9 / T9 / T10 — The corpus would damage three existing guards · **Refuted**

- **N8 — B0/B1 instruct re-duplication the repo de-duplicated on purpose.**
  `test_protected_path_governance_is_single_sourced`
  (`tests/tooling/test_corpus_contract.py:585-593`) exists because that prose
  *"was byte-identical across four agents until that skill absorbed it"*, and
  asserts exactly one carrier. Its glob is `.claude/**/*.md`, so a
  `src/**/AGENTS.md` corpus sits **outside the guard** — the duplication returns
  where the guard cannot see it. B1's instruction to restate the import-linter
  independence contract in four files is worse: that contract is **mechanised**
  at `pyproject.toml:388-390` and runs in `make gate`. Restating it in prose is
  spec-0022 R15 run backwards.
- **N9 / T9 — the heading-vocabulary claim is false, and adopting it weakens an
  existing guard.** `AGENT_SECTION_HEADINGS` (`tests/constants/corpus.py:587-599`)
  is exactly nine and **`## Owners` is not among them**; five of D3's six
  sections are new. Extending that one shared frozenset would let
  `.claude/agents/mango-*.md` carry `## Map` or `## Verify` and still pass
  `test_agent_headings_use_the_canonical_vocabulary` — the exact *"a duplicated
  section hides under a new name"* failure the constant's own comment
  (`:580-586`) pins at "Nine, not seven". **Requires a second constant and a
  second test, which is the second vocabulary the plan claims to avoid.**
- **T10 — `.claude/AGENTS.md` collides with two live globs.**
  `test_protected_path_governance_is_single_sourced` and
  `test_no_corpus_file_prescribes_the_retired_changelog_heading` (`:602-613`)
  both glob `.claude/**/*.md`. D0 proposes `.claude/AGENTS.md` while B0 requires
  the protected-path caveat **verbatim** — a direct invitation to a red build.

### F12 / T8 / T13 / T15 / N10 — Mechanics the rewrite must not inherit · **Refuted**

- **T13 — the mutation proof is a literal no-op.** § Verification runs
  `read_text().replace("## Boundaries", "## Notes", 1)` against the root
  `AGENTS.md`, which in PR A *is* today's `CLAUDE.md`. **`CLAUDE.md` has no
  `## Boundaries` heading** (its 17 `##` headings are listed in the review
  transcript). `str.replace` with no match returns the string unchanged, the
  file is rewritten byte-identically, and `make agents-md` exits **0**. The
  proof's `# MUST exit non-zero` fails and a reader concludes the gate is
  broken. This is `mango-mutation-proof`'s own headline failure
  (`SKILL.md:58-70`, "the mutation has to be the *defect*") reproduced inside
  the document citing the skill. Compounding it, the mutated check governs only
  directory files, and PR A has none.
- **T14 — the path mutation is right but one-sided.** No re-run asserting exit 0
  on the restored tree; `SKILL.md:130` requires both directions.
- **T8 — there are no helpers to reuse.**
  `test_agent_skill_owners_resolve_to_a_real_skill` (`:549-561`) is a set
  difference over a constant. The real helpers are module-private, and
  `_agent_paths()` shells out to `git ls-files` on **every** call (`:190-196`).
  A second module importing them is a private cross-module import and a
  subprocess multiplier. **Needs an extraction milestone first.**
- **T15 — A4 leads with a transcript, not a failing test**, unlike every other
  milestone.
- **N10 — A2's anti-duplication assertion is vacuous.** "No two files carry
  § Essential Commands" — it occurs in exactly one file today (`CLAUDE.md:9`),
  and C1 guarantees it stays there. Nothing in the plan can make it fire.
- **T6 — "~35 corpus-governance tests" is 33 functions**, but heavily
  parametrized over 27 slugs, so collected cases run into the high hundreds.
  **T7 confirms the plan's actual split (a new test module) is correct** — the
  criticism lands on D4's heading, not its content.
- **T2 — path existence partly exists.** `test_doc_links.py:84-98` already
  asserts every relative Markdown link in `_LINKED_DOCS` resolves, and
  `CLAUDE.md` is in it (`:37`). The genuinely new increment is **inline
  backticked** paths, since `_MD_LINK_RE` (`:50`) matches only `[text](target)`.
  Written as an extension, it inherits the non-vacuity guard at `:69-80` free.

### F13 / G3–G16 — Governance mechanics: four findings that become wrong commits

`mango-harness-dev` ran every claim rather than reading it, including a scratch
repo driven through `check_protected_paths.py`. Its verdict on the design is
that nothing in the plan's governance *design* is invalid — but four specifics
ship as defects.

- **G3 — the plan's own prose style silently defeats the trailer scoping.**
  `find_marker_scopes` (`scripts/_governance.py:162-176`) classifies a marker as
  scoped only when the first token is an **exact** member of the base-ref
  protected set; anything else sets `has_unscoped = True`, which approves
  **every** touched protected path (`:126`). Probed against the live table:

  | Trailer detail | Result |
  |---|---|
  | `pyproject.toml — comment only` | scoped ✔ |
  | `./pyproject.toml — comment only` | scoped ✔ (`normalize_repo_path`, `:140`) |
  | `` `pyproject.toml` — comment only `` | **unscoped → approves everything** |
  | `pyproject.toml, src/…/governance.py — …` | scoped to the **first only** |
  | `pyproject.tml` (typo) | **unscoped → approves everything** |

  **Every path in the plan is written in backticks.** A commit drafted from it
  inherits the habit and produces a trailer that reads narrow and behaves
  blanket — the exact opposite of D1's stated reason for choosing the form.
  (The behaviour is a recorded trade-off, `_governance.py:153-160`, and the gate
  does print `Approved by an unscoped marker`. It is still a trap.) **Rule to
  state: bare, unbackticked, first token, one trailer per path.**
- **G4 — the trailer requirement is self-inflicted.** D1 presents the two
  protected-path comment edits as forced. Nothing scans them:
  `CORPUS_DOC_RELPATHS` is a nine-entry documentation list not containing
  either file, and under D1's own design `CLAUDE.md` still exists, so a comment
  naming it is not even stale. Keep the comments and accept the trailers, or
  drop the edits — but do not present a choice as a constraint.
- **G7 — "expect the ConfigChange hook to fire; that is the mechanism working"
  is wrong.** The default is `off`
  (`src/mangomas/config/harness.py:26`), and `off` returns
  `ConfigAuditDecision("allow", …)` immediately
  (`src/mangomas/harness/config_audit.py:56-57`), logging at INFO and exiting OK.
  The process runs; it decides nothing. Worse, in `block` mode the hook fires on
  *the file changing*, not on who changed it — so it would block **the human
  step D2 prescribes**.
- **G12 — `SCRIPTS_TESTS` headroom, measured.** `--source=scripts` measures the
  whole directory at **636 statements / 144 branches, 96.28 %** against the
  pinned floor of 94. Solving `751/(780+N) ≥ 0.94` gives **N ≤ 18** uncovered
  units. Any real corpus linter exceeds that, and lowering the floor is not an
  escape: `test_isolated_coverage_floors_are_pinned`
  (`tests/deploy/test_ci_make_parity.py:375`) asserts `SCRIPTS_FLOOR == 94`.
  `CODE_PATHS` also includes `scripts` (`Makefile:10`), so the new script must
  pass `mypy --strict`.
- **G13 — D4's "import the inventory from `tests/constants/corpus.py` if
  importable" breaks the stdlib-only invariant it claims to honour.** Measured:
  `import tests.constants.corpus` pulls in **233 modules** and raises
  `ModuleNotFoundError` without `pydantic`, because
  `tests/constants/__init__.py` re-exports from `mangomas.config`. The
  `try/except` makes it worse: in CI's `lint` job `make install` has already
  run, so **the heavy path is the one CI exercises** and the stdlib fallback is
  never proven. Two code paths, two possible inventories. **Correction:** a
  standalone `tomllib`-readable inventory file, re-exported into `corpus.py` —
  the shape `[tool.mangomas.governance]` already uses, minus the protected-path
  cost.
- **G15 — B0's map of protected paths is wrong, in the milestone written to
  prevent wrong claims.** "Then `composition/`, `api/`, `adapters/`,
  `core/orchestrator/` … **four being protected paths**". Against
  `pyproject.toml:136-154`, the protected set contains **no** file under
  `composition/`, `api/` or `adapters/`; only `core/orchestrator/` qualifies,
  with two files. B0 requires each `Boundaries` section to state the
  protected-path obligation *verbatim*, so this guarantees three files
  asserting an obligation that does not exist — anti-pattern 2, in the plan's
  own taxonomy. Related: B1 files `harness/` as default-off, but
  `src/mangomas/harness/governance.py` is a protected path with no flag.
- **G8 — the `.claude/agents/` exclusion is doing safety work the plan
  describes as taste.** `AGENTS_GLOB` is `.claude/agents/**/*.md` — **any**
  `.md`, not just `mango-*.md`. Mutation-proved against a copy of the live
  tree: adding `.claude/agents/AGENTS.md` turns the frontmatter lint red
  (`missing frontmatter (expected leading ---)`, exit 1). The failure is loud,
  which is the right shape, but the prohibition belongs in the lint, not in an
  inventory row.
- **G16 — pre-existing gap the corpus would inherit.**
  `PROTECTED_PATH_OWNER_SLUGS` (`tests/constants/corpus.py:409-418`) omits
  `mango-harness-dev`, which declares ownership of four protected paths. The
  test it feeds would pass if it were added. B0 proposes extending that test to
  the new corpus; extending an incomplete roster propagates the gap. **Fix the
  constant first** — one line, no fallout.
- **G9 / G14, accepted without action.** The `PostToolUse --emit-path | ruff`
  hook is a measured no-op on `.md` (control file with deliberately
  mis-formatted Python came back byte-identical). And `Makefile`'s exclusion
  from `GOVERNANCE_SURFACE` is real but lives in a **module comment**
  (`governance.py:92-109`), not a docstring as the plan says, and **no test
  pins it** — `test_the_governance_surface_protects_itself` asserts only one
  direction. True today; not a guarantee.

**Independently spot-checked here:** `SCRIPTS_TESTS` omits `tests/tooling`
(`Makefile:24-28` ✓); `AGENT_SECTION_HEADINGS` has 9 members and `## Owners` is
not among them (✓); `CLAUDE.md` contains **zero** `## Boundaries` headings, so
T13's no-op verdict is confirmed (✓); `make gate` order ≠ CI `lint`-job order
(✓).

---

## 2. What survives

- **Mechanism before corpus**, and the inventory ratchet that keeps the gate
  green at every commit. Correct, and it survives every finding.
- **Naming the retirement precedent instead of routing around it.** The plan
  found `RETIRED_STRAY_AGENT_FILENAME`, quoted it, and argued in the open. The
  posture was right; F1 supplies the evidence the argument lacked, and it goes
  the other way.
- **Refusing the symlink** on Windows grounds, given PowerShell-first commands.
- **Protecting the dated records** from the rename.
- **C0**, strengthened per F8. The best engineering in the document.
- **A0**, which was the right instinct and is now executed.
- N-suggestion, adopted: C1's rule — *"An honest 250 beats a silent 710"* — is
  the best sentence in the plan and should survive verbatim.

---

## 3. Required shape of the rewrite

1. **Drop the 60-file `AGENTS.md` corpus.** Measured not to load (F1), guard
   provably insufficient (F2), scope unargued (F6), damages three existing
   guards (F11), and refused-in-advance by the plan of record (F10).
2. **Land the live-defect fixes first** (F3, F4, and T12's `make gate` ordering
   claim in `CLAUDE.md`). Small, standalone, and they prove the guard argument
   on real specimens.
3. **Strengthen the guards that failed to catch them** (F8's reverse-defaults
   test; F2/T3's bare-basename and AST symbol checks; T2's backtick extension to
   `test_doc_links.py`; the `_VANISHED_PATHS` entry). Valuable regardless.
4. **Take PR F from the plan of record for the root split**, citing it rather
   than re-deriving it, and attach probe 10 as the validation it never had.
   Extend its failing test to assert the import *resolves*, not just that the
   line is present.
5. **If per-directory files are wanted, name them `CLAUDE.md`** (F1 conclusion
   5) and hold them to one mechanically-checkable semantic claim per file.
   Applied honestly that admits perhaps three to five directories — which is the
   subject plan's own stated fallback.
6. **Do not write an ADR reversing the `agent.md` retirement.** Nothing that
   survives reverses it. If a record is wanted it is a short ADR for the root
   split boundary, citing PR F.

---

## 4. Method notes and limits

- The probe matrix ran against **one** binary (2.1.278) on Linux;
  `instructionFiles` semantics are eight days old. Re-run before the root PR
  merges and record the result in the ADR — a measurement with a date, not a law.
- Probes asked the model to introspect its own context: evidence of what reached
  the window, but self-report. The negative results (3, 5, 6, 9) are the
  load-bearing ones and are consistent with the positive controls (4, 7, 8, 10),
  which is the strongest form this method takes.
- `make gate` was **not** run end to end; `lint`, `typecheck`, `lint-imports`
  and the coverage floors were not executed. The full unit suite was, twice.
- The `mango-test-engineer` review had no shell, so its citations are file:line
  rather than command output. Its most consequential claims were spot-checked
  here (see the end of F13).
- All three specialist reviews (`mango-architect`, `mango-test-engineer`,
  `mango-harness-dev`) completed and are incorporated. They were run in
  parallel against the same commit and did **not** see each other's output;
  their independent convergence on F2/N1/T1 (the lint cannot catch the recorded
  failure) and on the `SCRIPTS_TESTS` omission is therefore corroboration
  rather than echo.
- Three findings in F13 (G3, G12, G15) and two in F8/F12 (the missing
  reverse-defaults direction, the no-op mutation) are defects that would have
  shipped. They are recorded here rather than only in the rewrite so the
  failure modes stay findable after the plan changes shape.
