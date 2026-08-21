# Harness governance, live corpus & package decomposition — delivery plan

- **Branch:** `claude/agents-mcps-implementation-plan-9bdtnt`
- **Date:** 2026-08-09
- **Target release:** `[Unreleased]` → next minor
- **Status:** In progress — PR A done; PR B complete (B1/B2a/B2b/B3+B4/B5 merged); PR C landed for R1–R3 (`config/`, `telemetry/`, `cli/`), R4 still deferred
- **Specs:** `specs/0017-protected-path-governance.md`,
  `specs/0018-live-claude-code-corpus.md`,
  `specs/0015-package-decomposition.md` (existing, amended)
- **ADRs:** ADR-0021, ADR-0023, ADR-0024 (PR B), ADR-0019 (amended). ADR-0022 was
  forward-referenced by ADR-0021 for a "harness governance port" that ADR-0021
  absorbed; it is a permanent gap, not the next free number.

## Executive summary

An audit plus five independent adversarial reviews found the repo's governance layer —
the protected-path gate, the harness streaming span — does not work, and the agent/skill
corpus (19 agents, 12 skills) is entirely inert because it targets a format Claude Code
never reads. This plan lands the fix as three sequential PRs (~20 commits total), each
independently green and shippable: **PR A** (harness governance, this document's
immediate scope), **PR B** (live `.claude/` corpus), **PR C** (spec-0015 package
decomposition, with its acceptance bar corrected). Full rationale, the five reviews'
findings, and everything deliberately deferred are recorded in
`/root/.claude/plans/create-a-plan-based-scalable-frost.md` (the approved plan) and are
summarized per-PR below as work proceeds.

## PR A — Protected-path governance (spec-0017 / ADR-0021, ADR-0023) — ✅ Done

### Milestone A0 — Scaffolding ✅
`specs/0017-protected-path-governance.md` (Implemented), `docs/adr/0021-protected-path-governance-contract.md`
(Accepted), `docs/adr/0023-workflow-implementation-reconciliation.md` (Accepted,
decision-only — no `dag` node implementation in this PR), this plan document.

### Milestone A1 — CI protected-path commit-trailer gate ✅
`pyproject.toml` `[tool.mangomas.governance]` protected-path set; new
`scripts/check_protected_paths.py`; `make protected-paths`; new CI job; `PROTECTED_PATHS`
in `lint_agent_frontmatter.py` reads the same TOML table. 10 tests in
`tests/test_check_protected_paths.py` exercise real git plumbing (temp repos), including
the regression guard that a marker in file *content* (not a commit message) still fails.

### Milestone A2 — Fix both dead hooks ✅
`--hook pre-tool-use` stdin-JSON mode in `lint_agent_frontmatter.py`; deferred
`pydantic`/`yaml` imports (`_SCHEMA_DEPS_AVAILABLE` guard); advisory `"ask"` JSON
decision; `PostToolUse` ruff hook fixed the same way via `--hook post-tool-use
--emit-path`; `.claude/settings.json` matcher widened to `Edit|Write|NotebookEdit`.

### Milestone A3 — Regression tests that exercise the real hook contract ✅
15 new tests in `tests/test_lint_agent_frontmatter.py` (in-process + subprocess-level,
clean `sys.path`, real stdin JSON) for both hooks, including a genuinely bare
interpreter with a stub `pydantic`/`pyyaml` that raises `ImportError` on import — proving
the original defect (`ModuleNotFoundError`, exit 1) is fixed. `tests/constants.py`
`PREEXISTING_HOOKS` updated (3 of 4 entries changed). New
`test_no_hook_command_references_the_dead_tool_input_env_var` regression guard.

### Milestone A4 — Streaming span fix ✅
`_HarnessOrchestrator.stream_dispatch` rewritten as `_traced_stream`, which
attaches/detaches OTel context per chunk (never across a `yield`) and closes the span in
a `finally` covering full drain, upstream exceptions, and early abandonment alike;
`contextlib.aclosing` added at the `api/routes/agents.py` consumer. 6 new tests in
`tests/test_composition.py`, including a demonstration that a naive shared-namespace
test fixture silently leaks state across tests (fixed by giving each test its own
`build_scoped_tracer` cache key) — the same class of isolation bug this milestone exists
to eliminate from the harness itself.

### Milestone A5 — Harness package port ✅
New `src/mangomas/harness/` (`governance.py` adapted to read
`pyproject.toml`'s `[tool.mangomas.governance]` instead of hardcoding the set;
`config_audit.py` ported near-verbatim from `origin/main`); `scripts/harness_config_audit.py`
adapted with deferred `mangomas.config`/`mangomas.telemetry` imports so it degrades
gracefully without `mangomas` installed; `ConfigChange` hook wired in
`.claude/settings.json`; one new `HarnessSettings.config_audit_mode` field. `main`'s
`coverage.py`/`harness_stop_gate.py` explicitly dropped (tautological on this branch —
see spec-0017 R7). 100% coverage on the new package; 24 new tests across
`tests/harness/` and `tests/test_harness_config_audit.py`.

### Milestone A7 — Measured `scripts/` coverage floor ✅
`make scripts-coverage` (mirrors `bridge-coverage`'s isolation idiom) + its own CI job;
floor set to the measured actual (85% with all A1-A5 tests, minus a 1-point safety
margin → `SCRIPTS_FLOOR ?= 84`) — `check_coverage.py` itself remains the drag at 24%,
noted as the natural next ratchet target.

### Milestone A8 — Reconciliation bookkeeping ✅
`specs/README.md` index rows for 0014-0017 + the ADR-0021/0023 supersession note;
`NEXT_STEPS.md` updated (reconciliation status, and a correction — there is no
`dispatch_loop` method, so composite loop bodies need no protected-path edit);
`CHANGELOG.md`'s duplicate `## [Unreleased]` heading collapsed and all four release
blocks reordered into correct descending semver order (verified via multiset diff: only
the duplicate heading + 2 now-redundant `---` separators were removed, zero content lost
or duplicated); `.claude/settings.local.json.example` template (JSON-validated by
`make validate-config`, contract-tested) plus a doc pointer in
`docs/tooling/claude-code-ecosystem.md`.

**`make gate` green end-to-end** (`validate-config lint format-check typecheck
frontmatter protected-paths test coverage bridge-coverage scripts-coverage`), full suite
1234 passed / 25 skipped (gated), zero test-file edits to any pre-existing test beyond
the documented `PREEXISTING_HOOKS` constant.

## PR B — Live corpus (spec-0018 / ADR-0024) — split into four

Four adversarial reviews resized this from one PR (~116 file touches, ~3× PR A — the
same silhouette that forced spec-0014's mid-flight descope) into four, and corrected
two load-bearing premises:

- **The corpus is not an invented format.** `.github/agents/**/*.agent.md` and
  `.github/skills/<name>/SKILL.md` are real, documented **GitHub Copilot** surfaces;
  `read`/`edit`/`search`/`execute` are documented Copilot tool aliases. Since VS Code
  Copilot *also* reads `.claude/agents/` and `.claude/skills/`, moving there serves both
  tools and only gives up the github.com cloud-agent surface. CHANGELOG records this
  under `Changed`, not `Removed`.
- **The real duplication is agent↔skill, not agent↔CLAUDE.md** (measured at 6–16 %, not
  the 50–60 % originally claimed). Agents that say *"use the X skill for the full recipe"*
  then restate the recipe — including, in one case, the same stale pointer in both copies.

| PR | Scope |
|---|---|
| **B1** | Skills go live: non-empty guard **first**, then a content-free `git mv`, skill roster test, doc sweep. ~20 files, no permission or routing design. |
| **B2** | Agents go live: unwired validators, content-free `git mv`, schema + frontmatter + least-privilege `tools`, path-scoped `permissions.deny`, contract tests with citation repairs folded in. |
| **B3+B4** | Content correctness, shipped as one PR. R7 enforced by four tests; 199 duplicated lines removed; the `mango-harness` skill absorbs governance prose that was byte-identical in four agents; the dead `### Breaking Changes` convention retired; two stray `agent.md` files promoted to nested `CLAUDE.md`, three deleted. **Superseded the original sketch**: measurement showed the gap was missing *owners*, not missing skills — the four unreferenced skills document surfaces no agent owns. Tracked as B5. |
| **B5** | Owner agents for the unowned surfaces: `eval/` (21 modules), `rag/` + `adapters/embeddings/` + `adapters/vector/`, `config.py`, `src/mangomas/agents/`, `secrets/`, `cli/`, `harness/`. Four mature skills already document these with no agent to reach for them. Additive capability change, deliberately kept out of the correctness PR. |

## PR C — Package decomposition (spec-0015, amended / ADR-0019, amended)

R1–R3 landed; R4 still deferred. Amended spec-0015's acceptance bar (a facade preserves
object identity, not module-global name bindings — `cli/main.py`'s `_build` and
`telemetry.py`'s lazy exporters need object-attribute seams, not bare functions, before
the split). Sequenced `config/` first (safest, zero monkeypatch risk) rather than last.
Keeps `parse_tool_call` as a delegation rather than deleting it (it is not dead —
`tests/test_tools.py` exercises it).

| Split | Shape | Seam sites | What it needed |
|---|---|---|---|
| `config/` → 12 domain modules | partition (AST-verified disjoint) | 0 | nothing — suite passed unmodified |
| `telemetry/` → 6 layers | layered DAG over shared mutable state | 6 (1 silent) | a pre-split safety commit |
| `cli/` → command package | one root, four command groups | 15 (**13 silent**) | a seam guard landed first |

The seam counts were predicted; the *silent* counts were measured, by neutralising each
patch and counting `build_orchestrator` calls. Thirteen CLI tests were building live
orchestrators and passing anyway. That is the finding worth carrying forward: on a split
of anything that is not a clean partition, a green suite is not evidence the facade
worked.

R4 (`core/structured.py`, plus the protected `core/tools.py` / `errors.py` batch) stays
deferred — it is a backwards-compatibility audit rather than a mechanical split, and it
is entangled with the unsettled question of who owns `harness/governance.py`, which
defines `PROTECTED_PATHS`.

## Verification

`make gate` green at every landing commit. Per-PR acceptance commands are enumerated in
the approved plan file; the headline ones:

```bash
# PR A — the gate that actually enforces
make protected-paths BASE_REF=origin/feat/initial-release

# PR A — the hook no longer crashes or reads a phantom env var
echo '{"tool_name":"Edit","tool_input":{"file_path":"src/mangomas/errors.py"}}' \
  | python scripts/lint_agent_frontmatter.py --hook pre-tool-use
```

## Deferred (rationale in the approved plan)

Composite `loop` bodies, the `dag` node kind's actual implementation, workflow node
plugins, dispatch-time agent profiles (tenancy Phase 2 + `model_override`), the
remaining 12 corpus agents/skills, ruff rule-family expansion, forcing `main` to match
the reconciled trunk, and `claude-mem`/`claude-hud`.
