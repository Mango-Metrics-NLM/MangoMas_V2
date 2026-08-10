---
name: mango-harness
description: >
  Protected-path governance and the Claude Code harness in Mango-Mas V2. Use
  when: editing a protected core contract and needing the BREAKING-CHANGE
  commit trailer, diagnosing a failed Protected-path governance gate, changing
  a hook in .claude/settings.json, or reasoning about what the PreToolUse hook
  can and cannot see. Covers the trailer contract, the advisory-vs-authoritative
  split between the hook and the CI job, and the four harness scripts.
argument-hint: "Name the protected file you are changing, or paste the failing gate output"
---

# Mango-Mas Harness Skill

## When to Use

- Editing `core/agent.py`, `core/orchestrator.py`, `core/tools.py`, `errors.py`
  or `registry.py` — the five protected paths
- The `Protected-path governance gate` CI job failed and you need to know why
- Adding or changing a hook in `.claude/settings.json`
- Deciding whether a change is genuinely breaking, or additive and marker-free

---

## Quick Commands

```powershell
# Run the authoritative gate exactly as CI does
make protected-paths BASE_REF=origin/feat/initial-release

# Ask the advisory hook what it would say about one file
echo '{"tool_name":"Edit","tool_input":{"file_path":"src/mangomas/errors.py"}}' | python scripts/lint_agent_frontmatter.py --hook pre-tool-use

# Validate the corpus and the shared config
python scripts/lint_agent_frontmatter.py
make validate-config
```

---

## The protected paths

Sourced from `pyproject.toml`'s `[tool.mangomas.governance]` table — the single
source of truth, read by `scripts/check_protected_paths.py`,
`scripts/lint_agent_frontmatter.py` and `mangomas.harness.governance` alike, so
the three cannot drift.

| Path | Owning agent |
|---|---|
| `src/mangomas/core/agent.py` | `mango-schema-evolution`, fuzz-targeted by `mango-hypothesis-fuzz` |
| `src/mangomas/core/orchestrator.py` | `mango-orchestrator-dev` |
| `src/mangomas/core/tools.py` | fuzz-targeted by `mango-hypothesis-fuzz` |
| `src/mangomas/errors.py` | `mango-error-taxonomy-dev` |
| `src/mangomas/registry.py` | — |

Note `src/mangomas/core/agent.md` is **not** protected; the table lists
`agent.py`. One character apart.

---

## The trailer contract

Editing a protected path requires a `BREAKING-CHANGE` marker on **at least one
commit message in the PR**. Without it, the `Protected-path governance gate` CI
job fails the build.

`pyproject.toml` also accepts the legacy `# approved-breaking-change` form. The
marker is matched line-anchored (`^\+?[ \t]*MARKER[ \t]*(:|$)`), so it is
recognised on an added diff line and *not* on a deleted one or mid-sentence in
prose.

The marker is a claim that the change is **deliberate and reviewed**, not a
formality to clear the gate. If the change is not actually breaking, prefer an
additive one that needs no marker at all. When a docs-only correction to a
protected file genuinely needs the trailer, say so plainly in the commit body
rather than letting the log imply a breaking change.

---

## Advisory hook vs. authoritative gate

This distinction is the whole point of the design, and getting it backwards
produces false confidence.

| | `PreToolUse` hook | CI gate |
|---|---|---|
| Script | `scripts/lint_agent_frontmatter.py --hook pre-tool-use` | `scripts/check_protected_paths.py` |
| Reads | one tool call's JSON on stdin | `git diff` / `git log` between PR base and head |
| Emits | `permissionDecision: "ask"` — **never `"deny"`** | exit 1 on a missing marker |
| Authority | **advisory only** | **authoritative** |

The hook matches `Edit|Write|NotebookEdit`. It therefore **cannot see a `Bash`
heredoc, a `>` redirect, or an MCP filesystem write** — a quiet session proves
nothing. Only committed history, which an in-session agent cannot rewrite, is
evidence.

Both hook modes are stdlib-only: they must work in an interpreter where the dev
extras are not installed, and must never crash a session over their own
plumbing.

---

## The four scripts

| Script | Role |
|---|---|
| `check_protected_paths.py` | The authoritative CI gate (`make protected-paths`) |
| `lint_agent_frontmatter.py` | Frontmatter lint (`make frontmatter`) + both hook modes + the legacy staged-diff `--check-protected-paths` for pre-commit |
| `harness_config_audit.py` | `ConfigChange` hook for `.claude/settings.json` edits; defers its `mangomas` imports so it degrades rather than crashing when the package is absent |
| `harness_session_start.py` | `SessionStart` probe (venv + LM Studio); always exits `EXIT_OK` |

---

## Runtime harness (`MANGOMAS_HARNESS__*`)

Separate from the governance scripts above: when `enabled=True`,
`composition.py::build_orchestrator` returns `_HarnessOrchestrator`, which wraps
`dispatch` and `stream_dispatch` in a `harness.agent_invoke` span.
`stream_dispatch`'s span attaches and detaches OTel context **per chunk** and
never across a `yield` — a span held across a yield leaks into the consumer's
own spans. See ADR-0021.

---

## Constraints

- DO NOT add a `DOCS-ONLY` marker alias. `breaking_change_marker_aliases` is not
  per-path scoped, so any new alias becomes a **universal bypass** of the gate.
- DO NOT treat a green session as evidence — the hook is advisory and blind to
  `Bash`.
- DO NOT hardcode the protected-path set; read it from `pyproject.toml`.
- DO NOT make the hook emit `"deny"`, or return a non-zero exit from a hook mode.

---

## Diagnosing Failures

1. `Protected-path governance gate` fails → no commit in the PR carries the
   marker. Amend a commit message or add one; the gate reads `git log`, not the
   diff.
2. The gate passes locally but fails in CI → `BASE_REF` differs. CI compares
   against the PR base; reproduce with
   `make protected-paths BASE_REF=origin/feat/initial-release`.
3. The hook never fires → it matches `Edit|Write|NotebookEdit` only. A `Bash`
   write bypasses it by design; that is what the CI gate is for.
4. `Could not read [tool.mangomas.governance]` warning → the loader is
   cwd-relative and fell back to its hardcoded set. Run from the repo root.
