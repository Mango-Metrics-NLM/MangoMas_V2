# Per-directory AGENTS.md corpus — delivery plan

- **Branch:** `claude/agent-md-documentation-u08et8` (plan only); one branch per PR block below
- **Date:** 2026-09-19
- **Target release:** rolling
- **Status:** Draft — **PR A milestone A0 is a go/no-go gate for the rest**
- **Specs:** `spec-0035` (owed — **unwritten**; takes the next free integer at creation time, per the root instruction file's § Spec-Driven Development. `0035` is free as of this writing; if a concurrent branch claims it, this plan's slug — `agents-md-corpus` — is the stable reference, not the number.)
- **ADRs:** `ADR-0035` (owed, and **load-bearing**: this plan reverses a recorded decision. See § The retirement precedent.)

## Executive summary

Add an `AGENTS.md` to every directory where an agent does work — 60 files
covering 83 directories — each carrying a scope statement, a local Mermaid
diagram, the `mango-*` sub-agent and skill that own the surface, and the
boundaries an agent must not cross.

Two constraints shape the sequencing, and both were discovered by reading the
repository rather than assumed from the request:

1. **This repository already tried per-directory agent files and retired them.**
   Five `agent.md` files were deleted, and `test_no_stray_agent_md_files_remain`
   mechanically prevents their return. The recorded reason —
   *"a file nothing loads cannot be kept honest"* — is exactly right, and it is
   the bar this plan has to clear rather than route around. § The retirement
   precedent argues that the 2026 `AGENTS.md` standard clears it, because the
   standard's whole point is that the files **are** loaded. That argument is the
   ADR, and if it does not survive review the corpus should not be built.
2. **spec-0022 R15** — a convention written only as prose decays. So the
   mechanism lands first (PR A), and each corpus PR afterwards grows an explicit
   inventory that CI enforces in both directions. A file cannot be added without
   being listed, and a listed directory cannot go missing.

The third finding is **visibility**: Claude Code 2.1.277 (2026-09-18 — one day
old) reads `AGENTS.md` only when no `CLAUDE.md` sits at or above the working
directory. This repo has a 710-line root `CLAUDE.md`, which under the default
setting would make the entire corpus invisible to the tool it is written for.
A0 probes this against the installed binary before a single content file is
written.

---

## The retirement precedent

**This is the part of the plan to review first.** Everything else is mechanics.

`tests/constants/corpus.py` records:

```python
# Five dormant `agent.md` files sat in the source tree, all containing claims
# that were false rather than stale. Two earned promotion to a nested
# CLAUDE.md; the other three were deleted as skill duplicates. Nothing may
# reintroduce the convention — a file nothing loads cannot be kept honest.
RETIRED_STRAY_AGENT_FILENAME: str = "agent.md"
```

and `tests/tooling/test_corpus_contract.py::test_no_stray_agent_md_files_remain`
fails the build if any `agent.md` reappears anywhere in the tree. The two
survivors were promoted to **nested `CLAUDE.md`** files, which is the convention
in force today:

| File | Lines | Shape |
|---|---|---|
| `src/mangomas/core/CLAUDE.md` | 58 | scope → file/protected table → editing rules → **Owners** (agent table) → Invariants |
| `tests/CLAUDE.md` | 132 | scope → Structure → baseline → Constants contract → Configuration → Run Commands → Fake reference |

Both are already in the `CORPUS_DOC_RELPATHS` stale-path ledger. So the
request as literally worded — `agent.md` in every directory — **would fail CI on
the first commit**, and should.

The case for proceeding anyway, which `ADR-0035` must make or the plan dies:

- **The retirement reason was "nothing loads it", not "per-directory is wrong".**
  The two files that *were* loaded survived and were promoted. The convention
  was retired for being dead weight, not for being the wrong idea.
- **`AGENTS.md` (plural) is not `agent.md` (singular).** It is a different
  filename under an open standard stewarded by the Linux Foundation's Agentic
  AI Foundation, and it **is** loaded — by Claude Code 2.1.277+, Codex, Cursor,
  Copilot and 30+ other tools, discovered nearest-first from the file being
  edited. The defect that killed `agent.md` is the precise defect the standard
  fixes.
- **The guard stays untouched.** `RETIRED_STRAY_AGENT_FILENAME` keeps forbidding
  `agent.md` forever. This plan adds `AGENTS.md`; it does not weaken, rename or
  except the existing test. If a reviewer's instinct is "didn't we delete
  these?" — the answer is yes, and the singular ones stay deleted.
- **Loaded is necessary, not sufficient.** The other half of "kept honest" is
  mechanical verification, which the five dead files never had. PR A supplies
  it before any content lands (§ D4), and the bar each proposed file must clear
  is: *would an agent working in this directory be wrong without it?*

If review rejects this reasoning, the correct outcome is to **extend the two
nested `CLAUDE.md` files** to a handful more directories and stop — not to build
60 files under a convention the repo has already judged once.

---

## Standards research (2026-09-19)

Recorded because the request was explicitly to meet today's standard, and so it
can be falsified later when the sources move.

### What the standard is

`AGENTS.md` is an open format for guiding coding agents — "a README for agents".
Formalised August 2025 (OpenAI, with Google, Cursor and Factory), donated to the
Linux Foundation's **Agentic AI Foundation** in December 2025, so the spec is no
longer vendor-owned. Adoption is reported at 60k+ repositories and 30+ tools.

- **Plain Markdown. No required fields, no YAML frontmatter, no special syntax.**
  A real constraint here: `.claude/agents/*.md` and `.claude/skills/*/SKILL.md`
  *do* require frontmatter and are linted for it by
  `scripts/lint_agent_frontmatter.py`. The new corpus must never be fed to that
  linter, and its own lint must **reject** frontmatter rather than require it.
  Two corpora, opposite rules — stated once, loudly, in `.claude/AGENTS.md`.
- **Nested files are the monorepo pattern.** Discovery runs from the touched
  file's directory up to the root; closest wins, the `.gitignore` mental model.
  The `openai/openai` monorepo is cited as carrying 88 of them.
- **Explicit user prompts override every file.**

### What the practitioner literature says

- **Length.** Sections under ~50 lines, whole file under ~150. Agents reliably
  act on the first 100–150 lines and probabilistically ignore the rest. The root
  `CLAUDE.md` is **710 lines** — an argument for pushing detail *down* into
  directory files rather than adding more at the root. Note both surviving
  nested files (58 and 132 lines) already sit inside this budget, which is mild
  evidence the convention was working.
- **The documented anti-patterns**, all of which this plan designs against:
  1. **Duplicate-README** — restating a human doc for a different audience in a
     second place that then drifts.
  2. **LLM-generated filler** — professional-looking files with wrong commands
     and hallucinated paths. *This is precisely how the five `agent.md` files
     died* ("a fictional `TurnRepository.save()`, an SSE format a client could
     not parse"), and it is the failure mode a 60-file agent-written corpus is
     most exposed to. D4's path-existence check is the mechanical answer.
  3. **Prose paragraphs and ambiguous directives** ("be careful") — ignored.
  4. **Silent drift** — prose fails no build. Third-party linters exist
     (`agents-lint`, AgentLinter); § D4 explains why we extend our own instead.
- **Mermaid is worth its tokens.** A diagram carries the load of several
  paragraphs at roughly 3–6× better token efficiency and is more precise to
  consume for agent and human alike. That settles the "with Mermaid" half of the
  request in favour of doing it, under the size discipline in D3.

### Claude Code specifics (the part that constrains us)

- Native `AGENTS.md` support shipped in **Claude Code 2.1.277, 2026-09-18**.
- Precedence is `/config` → **Project instructions**, i.e.
  `pluginConfigs["agents-md@builtin"].options.instructionFiles`, with four
  values: `managed-only`, `claude-md`, `claude-md-or-agents-md` (**default** —
  `AGENTS.md` read only when `CLAUDE.md` is absent), `claude-md-and-agents-md`.
- Nested `CLAUDE.md` files load **on demand** when Claude reads a file in that
  subtree, and are **concatenated, not overridden** — the root still applies.
  This differs from the standard's closest-wins rule, which is why A0 probes
  instead of assuming.
- The support shipped in the standalone CLI and desktop app and did **not**
  immediately extend to cloud-hosted deployments (Bedrock, Vertex AI, Foundry).
  Claude Code on the web — how much of this repo's agent work actually happens —
  is therefore not guaranteed to see `AGENTS.md` yet. **Another reason the
  `CLAUDE.md` filename must survive at the root.**

**Sources:** [agents.md](https://agents.md/) ·
[agentsmd/agents.md](https://github.com/agentsmd/agents.md) ·
[anthropics/claude-code `mods/agents-md`](https://github.com/anthropics/claude-code/tree/main/mods/agents-md) ·
[Claude Code: large codebases](https://code.claude.com/docs/en/large-codebases) ·
[Using CLAUDE.md files](https://claude.com/blog/using-claude-md-files) ·
[AGENTS.md Spec (2026) — morphllm](https://www.morphllm.com/agents-md-guide) ·
[Steering AI agents in monorepos — Datadog](https://dev.to/datadog-frontend-dev/steering-ai-agents-in-monorepos-with-agentsmd-13g0) ·
[AGENTS.md patterns — Crosley](https://blakecrosley.com/blog/agents-md-patterns) ·
[agents-lint](https://github.com/giacomo/agents-lint) ·
[Claude Code AGENTS.md fallback — runtimewire](https://runtimewire.com/article/claude-code-adds-agents-md-support) ·
[Mermaid for agent docs — mindstudio](https://www.mindstudio.ai/blog/mermaid-diagrams-claude-code-skills-context-compression/)

> Several of these domains are blocked by this environment's egress proxy
> (`agents.md`, `dev.to`, `morphllm.com`, `arxiv.org`), so their content above
> came from search-result summaries, not a full page read. **A0 re-verifies the
> two claims that gate the design** — the `instructionFiles` values and the
> nested-discovery behaviour — against the installed binary, not a blog.

---

## Design decisions

### D1 — the root file becomes `AGENTS.md`; `CLAUDE.md` becomes a one-line import

`git mv CLAUDE.md AGENTS.md` (content unchanged in PR A — trimming is C0, a
separate reviewable step), and `CLAUDE.md` becomes exactly:

```markdown
@AGENTS.md
```

| Option | Verdict |
|---|---|
| Symlink `CLAUDE.md` → `AGENTS.md` | **Rejected.** Windows needs elevation or developer mode; this repo's Essential Commands are PowerShell-first. A Windows clone gets a broken or literal-text file. |
| Keep both as real files | **Rejected.** Two copies of 710 lines is the duplicate-README anti-pattern with a guaranteed drift date. |
| Keep `CLAUDE.md` only | **Rejected.** Forfeits the vendor-neutral standard for Codex/Cursor/Copilot, which is the point. |
| Rename + `@AGENTS.md` import | **Chosen.** One source of truth, both filenames present, no symlink, Windows-safe, and the `CLAUDE.md` filename survives for cloud deployments that do not yet read `AGENTS.md`. |

**The blast radius is large and was measured, not estimated — 50+ files
reference `CLAUDE.md`.** The ones that are contracts rather than prose:

| Reference | Why it matters |
|---|---|
| `tests/deploy/test_env_example_contract.py` (10) | Parses `MANGOMAS_*` names **out of `CLAUDE.md`** and asserts both directions against `Settings`, plus documented defaults vs live field values. Hard-codes `_CLAUDE_MD`. **Also blocks C0 — see there.** |
| `tests/constants/corpus.py` (7) | `CORPUS_DOC_RELPATHS` names `CLAUDE.md` and both nested files for stale-path checking. |
| `tests/tooling/test_live_path_ledger.py` | `_LIVE_FILES` hard-codes `CLAUDE.md`. |
| `pyproject.toml` (3) | Comments only — but a **protected path**: the edit needs a `BREAKING-CHANGE` trailer. |
| `src/mangomas/harness/governance.py` (1) | Comment only — also **protected**, same trailer. |
| `scripts/lint_agent_frontmatter.py` (3) | Comments/messages. |
| ~45 prose files | `README.md`, `CONTRIBUTING.md`, `NEXT_STEPS.md`, `.claude/skills/*/SKILL.md`, specs, ADRs, plans. Dated records (CHANGELOG, plans, ADRs, specs) are **historical and must not be rewritten** — the repo's own ledger convention. Only live docs change. |

Because two protected paths are touched for comment text alone, **PR A carries a
`BREAKING-CHANGE: <path> — <rationale>` trailer per path**, using the
path-scoped form so it approves only what it names.

### D2 — nested visibility is probed, then mechanised

If A0 shows nested `AGENTS.md` is not read while a root `CLAUDE.md` exists, PR A
sets project-scoped `instructionFiles: "claude-md-and-agents-md"`.

Two frictions, both real, both flagged so nobody is surprised at review:

1. `.claude/settings.json` sits in its own `permissions.deny`
   (`Edit(/.claude/settings.json)`), so **an agent cannot make this edit. It is
   a human step**, and the plan says so rather than pretending otherwise.
2. The `ConfigChange` hook (`scripts/harness_config_audit.py`) audits that file
   under `MANGOMAS_HARNESS__CONFIG_AUDIT_MODE`. Expect it to fire — that is the
   mechanism working.

`make validate-config` and `test_corpus_contract.py`'s hook-survival assertions
must stay green afterwards; every `.claude/settings.json` edit must be additive.

### D3 — the content contract

Derived from `src/mangomas/core/CLAUDE.md`, which already works, plus the
Mermaid map the request asks for. **≤150 lines, ≤50 lines per section, no
frontmatter**, fixed section order so the shape is greppable and lintable.

````markdown
# <dir path> — <one-line purpose>

## Scope
<2–4 sentences. What lives here, what does not. Link — never restate — the
 root AGENTS.md, spec or ADR that governs it.>

## Map
```mermaid
flowchart LR
  ...  %% ≤12 nodes: this directory plus one hop out
```

## Owners
| Surface | Agent | Skill |
|---|---|---|
| <file or seam> | `mango-<slug>` | `mango-<skill>` |

## Invariants
<Imperative, checkable rules specific to THIS directory. Nothing already true
 repo-wide — that belongs in the root file.>

## Boundaries
<What an agent must not do here: protected paths, layering direction,
 default-off flags, back-compat obligations.>

## Verify
```bash
<the narrowest command that proves a change here is sound>
```
````

`Owners` and `Invariants` keep the exact heading names the surviving nested file
uses, so `test_agent_headings_use_the_canonical_vocabulary` can be extended to
this corpus instead of learning a second vocabulary.

Two rules that keep this from becoming filler:

- **Pointers, not copies.** A fact in the root `AGENTS.md`, a spec or an ADR is
  linked, never restated. D4 makes that literal.
- **Local diagrams only.** The `Map` shows what is *inside* this directory plus
  immediate neighbours. It is **not** a re-draw of
  `docs/architecture/c2-container.md` or `c3-component.md`, which stay
  authoritative for system-level views and are linked instead of competed with.
  `tests/tooling/test_architecture_docs.py` already guards those.

### D4 — extend the existing corpus governance; do not build a parallel one

**`tests/tooling/test_corpus_contract.py` already carries ~35 corpus-governance
tests** — roster set-equality both directions, `test_every_skill_has_a_skill_md`,
`test_agent_skill_owners_resolve_to_a_real_skill`,
`test_every_source_surface_has_a_write_capable_owner`,
`test_agent_headings_use_the_canonical_vocabulary`,
`test_docs_do_not_reference_a_retired_corpus_path`. Plus
`test_live_path_ledger.py` for stale paths. **Most of what a new AGENTS.md lint
needs already exists and is already wired into `make test`.** Building a second
system beside it would be the duplication this plan is supposed to prevent.

So the split is:

**In `tests/tooling/test_agents_md_corpus.py` (new, pytest — the bulk):**

1. **Inventory, both directions** against `AGENTS_MD_DIRS` in
   `tests/constants/corpus.py`, mirroring `EXPECTED_AGENT_SLUGS`' set-equality
   style so editing the tuple *is* the review record.
2. **Path existence** — every repo-relative path mentioned in a file exists.
   The single highest-value check: the mechanical answer to the hallucinated
   paths that killed the `agent.md` corpus.
3. **Agents and skills resolve** — every `mango-*` name maps to a real
   `.claude/agents/mango-*.md` or `.claude/skills/*/SKILL.md`. Reuses
   `test_agent_skill_owners_resolve_to_a_real_skill`'s helpers.
4. **Structure** — required sections in order, no YAML frontmatter, ≤150 lines,
   ≤50 lines per section.
5. **Mermaid** — every fence closed, known diagram type, ≤12 nodes.
6. **Exemptions explicit** — a directory covered by a parent is listed in
   `AGENTS_MD_EXEMPT` naming the covering parent, never silently absent.
7. **Ledger extension** — add the corpus to `CORPUS_DOC_RELPATHS` and
   `_LIVE_FILES` so existing retired-path and vanished-module checks cover it
   for free. This is most of check 2 at near-zero cost.

**In `scripts/lint_agents_md.py` + `make agents-md` (new, thin):** only the
subset that must run **outside pytest** — the `PreToolUse`/CI path where
`mangomas` may not be installed. Stdlib-only, like
`scripts/lint_agent_frontmatter.py`'s hook modes, with that script's
`EXIT_OK`/`EXIT_SCHEMA` exit-code convention. It re-implements nothing: it
imports the inventory from `tests/constants/corpus.py` if importable and
falls back to a glob otherwise.

Inventory lives in **`tests/constants/corpus.py`**, beside the agent and skill
rosters it mirrors — not in `pyproject.toml`, which is protected and would drag
a trailer onto every corpus PR. `scripts/` has its own isolated coverage gate
(`make scripts-coverage`, `SCRIPTS_FLOOR ?= 94`), so the new script needs real
tests, not smoke coverage.

---

## Directory inventory

83 directories: **60 get their own `AGENTS.md`**, 23 are covered by an explicit
parent entry. `.claude/skills/<name>/` subdirectories are excluded outright —
each already carries a `SKILL.md`, which *is* that directory's agent-facing
contract; a second file beside it is the duplicate-README anti-pattern by
construction.

| Tier | Directories | Files | Covered by parent |
|---|---|---|---|
| Root | `.` | 1 | — |
| 1 — source | `src/mangomas` + 30 subpackages | 31 | `src/` (namespace only) |
| 2 — tests | `tests/` + 22 subdirs | 20 | `tests/eval/fixtures`, `tests/mango_contracts/fixtures`, `tests/snapshots` (data, not code) |
| 3 — tooling | `scripts`, `specs`, `docs`, `deploy`, `examples`, `eval_harness_bridge`, `mango-integration-contracts/src/mango_contracts`, `.claude` | 8 | `docs/*` (9), `examples/workflows`, `eval_harness_bridge/{src,config,datasets}`, `mango-integration-contracts` + `/src`, `.claude/{agents,skills}`, `typings` + `/hypothesis` |
| **Total** | **83** | **60** | **23** |

Every file must clear the retirement bar: *would an agent working in this
directory be wrong without it?* A file that only restates its own filenames
fails that test and should become an `AGENTS_MD_EXEMPT` entry instead. **The
60 is a ceiling, not a quota** — if B and C land 48 honest files and 12
exemptions, that is a better outcome than 60, and the inventory records it
either way.

The four thinnest source leaves — `adapters/llm/vertex`, `core/orchestrator`,
`workflow/predicate`, `utils` (1–2 modules each) — still earn a file, because
each carries a genuinely local rule: lazy SDK import under the `vertex` extra;
protected-path status; the never-raises closure contract.

---

## PR A — mechanism first (spec-0035, ADR-0035)

No directory content lands here. The point is that the corpus cannot be added
unguarded — the thing the five dead `agent.md` files never had.

### Milestone A0 — the go/no-go probe

- **Failing test first:** none, and saying so is honest — this is an
  investigation. Its **output is a recorded finding** in `ADR-0035`: the
  installed `claude --version`, the four `instructionFiles` values as the binary
  reports them, and an observed answer to *"with a root `CLAUDE.md` present, is
  a nested `AGENTS.md` loaded when a file in that subtree is read?"*
- **Depends on:** nothing. Blocking for everything else.
- Probe in a scratch fixture tree, never the repo.
- **Record the negative result too.** If nested `AGENTS.md` is invisible under
  the default setting, D2's settings change moves from optional to required —
  and if it is invisible under *every* setting, **the plan stops here** and the
  correct answer is to extend the nested `CLAUDE.md` convention instead. A
  corpus no tool loads is the precise thing this repo already deleted once.

### Milestone A1 — ADR-0035, the argument

- **Failing test first:** `tests/tooling/test_decision_record_numbering.py`
  already enforces ADR numbering; the new record must satisfy it.
- **Depends on:** A0.
- `docs/adr/0035-agents-md-as-root-instruction-file.md`, drafted by
  `mango-adr-author`, making the § The retirement precedent case explicitly and
  citing A0's measured result.
- **This milestone is the review gate.** If the ADR is rejected, PRs B–D do not
  happen and `spec-0035` is never written.

### Milestone A2 — root file swap

- **Failing test first:** `tests/tooling/test_agents_md_root.py` — asserts
  `AGENTS.md` exists at the root, `CLAUDE.md`'s non-whitespace content is
  exactly `@AGENTS.md`, and no two files carry the § Essential Commands heading
  (anti-duplication). Red now: `AGENTS.md` does not exist.
- **Depends on:** A1.
- `git mv CLAUDE.md AGENTS.md`; write the one-line `CLAUDE.md`.
- Repoint the contracts in D1's table: `test_env_example_contract.py`'s
  `_CLAUDE_MD`, `corpus.py`'s `CORPUS_DOC_RELPATHS`,
  `test_live_path_ledger.py`'s `_LIVE_FILES`, and the two protected-path
  comments (with the path-scoped `BREAKING-CHANGE` trailers).
- Fix live prose references only. **Leave CHANGELOG, plans, ADRs and specs
  alone** — they are dated records.
- Add § Agent instruction files to the root file: D3's contract, closest-wins,
  the pointer to `make agents-md`, and one sentence on why `agent.md` stays
  retired.

### Milestone A3 — the corpus lint

- **Failing test first:** `tests/tooling/test_lint_agents_md.py` — one case per
  D4 check, each fed a fixture violating exactly that rule, asserting non-zero
  exit with the offending path named. Red now: no script.
- **Depends on:** A2.
- `tests/tooling/test_agents_md_corpus.py` + `AGENTS_MD_DIRS` /
  `AGENTS_MD_EXEMPT` in `tests/constants/corpus.py`, seeded with the **root
  file only**. Each corpus PR below appends its own directories — that is the
  ratchet: green at every commit, and the corpus can only grow.
- `scripts/lint_agents_md.py` for the out-of-pytest subset.

### Milestone A4 — wire the gate, and prove it bites

- **Failing test first:** the `mango-mutation-proof` loop, run for real and
  recorded in the PR body (see § Verification). A gate nobody has watched fail
  is not evidence.
- **Depends on:** A3.
- `Makefile`: add `agents-md`, insert into `gate` after `frontmatter`.
  (`Makefile` is deliberately **not** protected — see the `GOVERNANCE_SURFACE`
  docstring — so no trailer.)
- `.github/workflows/ci.yml`: add the step to the `lint` job after
  `Frontmatter lint`. `tests/deploy/test_ci_make_parity.py` asserts CI and the
  `Makefile` agree, so both move together or it fails.
- `make scripts-coverage` must still clear `SCRIPTS_FLOOR`.
- `CHANGELOG.md` under `[Unreleased] / Added`.

---

## PR B — `src/mangomas/` corpus (31 files)

### Milestone B0 — migrate the survivor, then the load-bearing packages

- **Failing test first:** extend the `AGENTS_MD_DIRS` inventory with these
  directories before the files exist; the corpus test goes red on the gap.
- **Depends on:** PR A.
- **`src/mangomas/core/CLAUDE.md` → `AGENTS.md` first**, adding only the `Map`
  diagram and the skill column. It already satisfies D3 in substance; migrating
  it first proves the contract fits reality rather than the reverse. Update
  `CORPUS_DOC_RELPATHS`.
- Then `composition/`, `api/`, `adapters/`, `core/orchestrator/` — where
  `Boundaries` matters most, four being protected paths.
- Each `Boundaries` section names the protected-path obligation **verbatim** and
  links `.claude/skills/mango-harness/SKILL.md`.
  `test_protected_path_owners_point_at_the_governance_skill` already enforces
  this for agents; extend it to the corpus.
- Draft via the owning sub-agent (`mango-orchestrator-dev`,
  `mango-api-impl-dev`, `mango-llm-adapter-dev`, …) so boundary text comes from
  the agent that owns the surface, not a fresh reading. This is the defence
  against anti-pattern 2.

### Milestone B1 — opt-in subsystems

- **Failing test first:** inventory ratchet.
- **Depends on:** B0 (reuses its file shape).
- `workflow/` (+`nodes/`, `predicate/`), `eval/` (+`scorers/`, `sinks/`,
  `sources/`, `targets/`), `rag/`, `cognitive/`, `secrets/`, `harness/`,
  `telemetry/`.
- All default-off. Each `Boundaries` states the flag and the identity
  obligation: dispatch stays byte-identical with the flag off. The import-linter
  independence contract (`workflow` / `eval` / `rag` / `cognitive` may not
  import each other) is restated in all four — the rule most often broken by
  accident.

### Milestone B2 — the remaining packages

- **Failing test first:** inventory ratchet; closes `src/mangomas/**`, so a new
  package added later without an `AGENTS.md` fails the gate.
- **Depends on:** B1.
- `agents/`, `adapters/{llm,llm/vertex,storage,embeddings,vector}/`,
  `api/{routes,middleware}/`, `cli/` (+`commands/`), `config/`, `utils/`.

---

## PR C — `tests/` corpus (20 files) + root trim

### Milestone C0 — teach the env contract to read the corpus

- **Failing test first:** move one `MANGOMAS_*` row out of the root file into
  `src/mangomas/rag/AGENTS.md`. `test_env_example_contract.py` goes **red** —
  it asserts every `Settings` field appears in the root file's config tables.
- **Depends on:** PR B.
- **This is why C1's trim cannot be a footnote.** The 710-line root file is not
  bloat: ~150 lines of it are a *mechanically enforced* config contract, in
  both directions, including documented defaults compared against live field
  values. Moving those rows down requires the test to scan the corpus, keeping
  both directions and the defaults check intact.
- Land this before any trimming. A weakened contract is a worse outcome than a
  long root file, and the repo has been bitten by exactly that
  ("following CLAUDE.md got a weaker check than the gate").

### Milestone C1 — trim the root toward the budget

- **Failing test first:** extend the ≤150-line check to the **root** file
  (A3 applies it to directory files only). Red at 710 lines.
- **Depends on:** C0.
- Move, do not delete: per-package config rows to their packages' files; agent
  and skill rosters become pointers to `.claude/AGENTS.md`. **Essential
  Commands and the Key Design Rules table stay at the root** — they are
  directory-independent.
- If the trim cannot reach 150 without losing something load-bearing, **raise
  the root budget in the lint with a comment saying why**, rather than quietly
  exempting the root. An honest 250 beats a silent 710.

### Milestone C2 — the gated suites

- **Failing test first:** inventory ratchet.
- **Depends on:** PR A only — parallel-safe with B and C0/C1.
- `integration/`, `lmstudio/`, `postgres/`, `vertex/`, `rag/`,
  `eval_harness_bridge/`, `mango_contracts/`, `deploy/`, `regression/`.
- Each names its gate env var (`RUN_INTEGRATION`, `RUN_LMSTUDIO`, `RUN_RAG`, …)
  and its external prerequisite in `Verify`. Highest-value tier in the corpus:
  it is where an agent most often runs the wrong command and concludes a suite
  is broken when it simply never ran.

### Milestone C3 — the unit suites

- **Failing test first:** inventory ratchet; closes `tests/**`.
- **Depends on:** C2.
- **`tests/CLAUDE.md` → `AGENTS.md`** (the second survivor), plus `adapters/`
  (+2), `agents/`, `cognitive/`, `composition/`, `constants/`, `eval/`,
  `harness/`, `tooling/`.
- At 132 lines `tests/CLAUDE.md` is near the budget, so the `Map` diagram
  arrives with a trim of its Run Commands block, which duplicates `make help`.
- `tests/constants/AGENTS.md` states the re-export contract (`X as X`, never
  restate) explicitly — subtle, enforced, and easy to violate in good faith.

---

## PR D — periphery (9 files) + adoption

### Milestone D0 — tooling and governance directories

- **Failing test first:** inventory ratchet; closes the table.
- **Depends on:** PR A.
- `scripts/`, `specs/`, `docs/`, `deploy/`, `examples/`, `eval_harness_bridge/`,
  `mango-integration-contracts/src/mango_contracts/`, `.claude/`.
- `docs/AGENTS.md` routes to its 9 subdirectories rather than spawning 9 stubs;
  each is listed in `AGENTS_MD_EXEMPT` with `docs` as covering parent — the
  exemption mechanism used as designed, in the open.
- `.claude/AGENTS.md` is the interesting one. It must state that the files
  *underneath it* are the one place in the repo where YAML frontmatter is
  **required**, that `make frontmatter` — not `make agents-md` — is their gate,
  and that `agent.md` remains retired. Two corpora, opposite rules, one
  sentence each.

### Milestone D1 — Mermaid validation

- **Failing test first:** a fixture with a syntactically invalid Mermaid body;
  `make agents-md` must exit non-zero. Red until the check exists.
- **Depends on:** D0 — all diagrams present, so the check runs against the real
  corpus on first green.
- A3's check is structural (fence closed, known type, ≤12 nodes). This upgrades
  it to a parse. Prefer a vendored/stdlib grammar check; if a real parse is not
  cheap, **keep the structural check and record the limitation in the ADR**
  rather than adding a Node toolchain to a Python repo for 60 diagrams.
- The `Mermaid Chart` MCP validator is available in agent sessions and is useful
  while drafting. It is **not** a CI gate and must not be described as one.

### Milestone D2 — adoption and cross-references

- **Failing test first:** extend `test_agents_md_root.py` to assert `README.md`
  links the corpus, and that every `.claude/agents/mango-*.md` naming a `src/`
  path points at that directory's `AGENTS.md` — the mirror of the existing
  `test_mapped_agent_references_its_skill`.
- **Depends on:** D1, PR B, PR C.
- `README.md` § Documentation gets the corpus entry (`test_doc_links.py` guards
  those links).
- Each `mango-*` agent body gains a one-line pointer to the `AGENTS.md` of the
  surface it owns — the bidirectional link that keeps ownership honest in both
  directions.
- `CHANGELOG.md` final entry; mark this plan **Done**.

---

## Deferred / out of scope

- **Generating `AGENTS.md` from docstrings.** Tempting — it would kill drift at
  the root. Deferred because the valuable content (boundaries, ownership, "do
  not do this") exists nowhere in the source to generate from. Revisit only with
  a spec saying what the generator reads.
- **Replacing `docs/architecture/c2`–`c4` with per-directory diagrams.** Those
  are system-level C4 views with a different audience, guarded by
  `test_architecture_docs.py`. Directory diagrams link to them. Re-opening needs
  an ADR.
- **Un-retiring `agent.md`.** Never. `RETIRED_STRAY_AGENT_FILENAME` and its
  test are untouched by this plan, and `ADR-0035` must say so explicitly.
- **Per-skill `AGENTS.md` under `.claude/skills/<name>/`.** Excluded by
  construction: `SKILL.md` already is that file.
- **Third-party linters** (`agents-lint`, AgentLinter). Evaluated, not adopted:
  the checks worth having here are repo-specific — does this `mango-*` agent
  exist, is this a protected path, does this env var appear in `Settings` — and
  none are checks a generic linter can make. Re-proposing one means showing a
  check it makes that D4's does not.
- **`instructionFiles` as a committed project setting** *if* A0 shows nested
  `AGENTS.md` already loads with a root `CLAUDE.md` present. Then D2's edit is
  unnecessary and must not be made — an unneeded config write is a standing
  liability against `permissions.deny` and the `ConfigChange` hook.

## Verification

```bash
make gate                 # full pre-PR chain in CI's order; now includes agents-md
make agents-md            # the new gate alone
make scripts-coverage     # the new script must clear SCRIPTS_FLOOR (94)
make frontmatter          # unchanged — proves the two corpora stayed separate
make validate-config      # only if D2's .claude/settings.json edit was needed
python -m pytest tests/tooling tests/deploy -q   # corpus + env-contract suites
```

Per-PR mutation proof (`.claude/skills/mango-mutation-proof/SKILL.md`), run and
recorded in each PR body:

```bash
cp AGENTS.md /tmp/AGENTS.md.bak
python - <<'PY'
import pathlib
p = pathlib.Path("AGENTS.md")
p.write_text(p.read_text().replace("## Boundaries", "## Notes", 1))
PY
make agents-md            # MUST exit non-zero
cp /tmp/AGENTS.md.bak AGENTS.md
make agents-md            # MUST exit 0
```

And the check that matters most, because it is the one the retired corpus
failed — prove the path-existence guard bites:

```bash
printf '\nSee `src/mangomas/does_not_exist.py`.\n' >> AGENTS.md
make agents-md            # MUST exit non-zero, naming the path
git checkout AGENTS.md
```
