# ADR-0035: AGENTS.md as the root instruction file

## Status

Proposed

## Context

The root instruction file was a single 720-line `CLAUDE.md`, of which most —
commands, architecture, design rules, the `MANGOMAS_*` tables, conventions — is
vendor-neutral, and a minority describes Claude Code's own surfaces. `AGENTS.md`
is read by 30+ agents; Claude Code is the exception that reads `CLAUDE.md`. This
implements **PR F** of `docs/plans/20260916T214636Z-reliability-evidence-plan.md`
(lines 284-296), the plan of record; the decision is not re-argued here.

What was unknown was discovery behaviour, so it was measured. Ten fixture trees
(`git init`, canary token per instruction file), queried with `claude -p` against
Claude Code **2.1.278** on Linux, 2026-09-19:

| # | Root file(s) | Nested file | `instructionFiles` | Nested loaded? |
|---|---|---|---|---|
| 1 | `CLAUDE.md` | — (no tool use) | default | nothing nested loads eagerly |
| 2 | `CLAUDE.md` | `sub/CLAUDE.md` + `sub/AGENTS.md` | default | CLAUDE.md yes, AGENTS.md no |
| 3 | `CLAUDE.md` | `solo/AGENTS.md` only | default | no |
| 4 | `AGENTS.md` (no CLAUDE.md) | `solo/AGENTS.md` | default | yes |
| 5 | `CLAUDE.md` | `solo/AGENTS.md` | project `settings.local.json` | no — setting inert |
| 6 | `CLAUDE.md` + `AGENTS.md` | — | project settings | root AGENTS.md no — inert |
| 7 | `CLAUDE.md` + `AGENTS.md` | — | user `~/.claude/settings.json` | root AGENTS.md yes |
| 8 | `CLAUDE.md` | `solo/AGENTS.md` | user settings | yes |
| 9 | `CLAUDE.md` = `@AGENTS.md` + `AGENTS.md` | `solo/AGENTS.md` | default | root yes, **nested no** |
| 10 | `CLAUDE.md` = `@AGENTS.md` + content, + `AGENTS.md` | `solo/CLAUDE.md` | default | **all three yes** |

## Decision

Split the root instruction file. `AGENTS.md` holds the vendor-neutral half;
`CLAUDE.md` keeps only Claude Code's own surfaces (agent corpus, harness, skills,
MCP servers) and imports the other half with a first-line `@AGENTS.md`. Measured
after the split: `AGENTS.md` 503 lines, `CLAUDE.md` 243 lines.

## Consequences

### Positive

- Probe 10 is the shipped design, validated end to end.
- Nested instruction files are always lazy (probe 1), so cost is roughly one file
  per subtree touched, not the whole corpus. Measured: root pair ~11.3k tokens;
  the two existing nested files 778 and 1451 tokens.
- Five guards bind the split: `test_claude_md_first_line_imports_agents_md`,
  `test_the_agents_md_import_resolves`,
  `test_the_split_puts_each_section_on_the_right_side`,
  `test_no_section_heading_is_duplicated_across_the_pair`,
  `test_no_nested_agents_md_files`.

### Negative / Trade-offs

- Any root `CLAUDE.md` — **including a one-line `@AGENTS.md` pointer** (probe 9) —
  disables nested `AGENTS.md` discovery entirely. **Per-directory instruction
  files in this repo are therefore named `CLAUDE.md`.**
- `instructionFiles: claude-md-and-agents-md` works only from
  `~/.claude/settings.json` (probes 7-8) and is inert in project settings
  (probes 5-6). It can never be a committed project mechanism — contributor
  convenience only.
- Honest residue: prose that is rationale or "why" is not mechanically checkable.
  The contract tests cover names, defaults, paths, sections and the import
  resolving — not whether a boundary explanation is still true.

### Neutral

- **Expiry.** Measured on one binary version on Linux, with `instructionFiles`
  semantics 8 days old at measurement. Re-measure before relying on it: this is a
  measurement with a date, not a law.
- The `agent.md` retirement is not reversed. `RETIRED_STRAY_AGENT_FILENAME` and
  `test_no_stray_agent_md_files_remain` are untouched.
- `tests/deploy/test_env_example_contract.py` now reads the **union** of the pair
  (`ROOT_INSTRUCTION_RELPATHS`); otherwise a config row moving across the split
  would vanish from a ~110-row contract while both files still looked healthy.

## Alternatives Considered

- **A 60-file nested `AGENTS.md` corpus** — rejected: probe 9 shows it would be
  invisible, reproducing the defect that retired `agent.md` ("a file nothing loads
  cannot be kept honest").
- **`instructionFiles` in project settings** — rejected: inert (probes 5-6).
- **Keep one 720-line `CLAUDE.md`** — rejected: every non-Claude agent reads nothing.
- **Duplicate the shared half into both files** — rejected: two sources of truth,
  which is why the heading-duplication guard exists.

## References

- Plan of record: `docs/plans/20260916T214636Z-reliability-evidence-plan.md`
  lines 284-296 (PR F)
- Files: `AGENTS.md`, `CLAUDE.md`
- Tests: `tests/test_agents_md_contract.py`,
  `tests/deploy/test_env_example_contract.py`, `tests/constants/corpus.py`
- Related ADRs: ADR-0024 (live corpus surface and posture), ADR-0033 (boundary
  honesty)
- External: `AGENTS.md` is stewarded by the Agentic AI Foundation under the Linux
  Foundation (Codex, Copilot, Cursor, Gemini CLI, Jules, Aider, Zed, Windsurf, Devin)
