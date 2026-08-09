# Spec-0016: Claude Code ecosystem tooling integration

- **Status:** In progress — `.mcp.json`, the `rtk` hook, guardrails, and this
  documentation are landed; `claude-hud`'s disposition and `claude-mem`'s
  hook wiring each require one hands-on step outside this session (see
  Acceptance criteria) before this spec moves to Implemented.
- **Linked ADR:** ADR-0020 (Claude Code ecosystem tooling integration)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

Seven external Claude Code ecosystem repos were researched for integration
as developer-tooling (MCP servers, hook-based utilities, a statusline plugin,
a repo-packing tool, a memory tool, a curated resource list). None of them
are `src/mangomas/` dependencies — they configure the Claude Code environment
contributors use to work on this repo. The intended outcome is a richer,
safer tool surface (file/git/fetch/sequential-thinking/repomix MCP access,
token-efficient Bash output, cross-session memory) wired through this repo's
existing conventions rather than bolted on ad hoc, with one external tool
explicitly rejected (redundant + third-party data egress) and one treated as
a reference source rather than software.

## Requirements

- R1 — Register `filesystem`, `git`, `fetch`, `sequential-thinking`, and
  `repomix` as project-scoped MCP servers via `.mcp.json`, each pinned to an
  exact version, `filesystem`/`git` scoped to `${CLAUDE_PROJECT_DIR:-.}`.
- R2 — Add `rtk`'s `PreToolUse`/`Bash` hook to `.claude/settings.json`,
  additive to the four pre-existing hooks, gated on `MANGOMAS_DISABLE_RTK_HOOK`
  so it has a real per-contributor opt-out despite Claude Code's lack of a
  per-hook disable mechanism.
- R3 — Add `claude-mem`'s lifecycle hooks the same way, sourced from an
  observed installer diff (never hand-authored), gated on
  `MANGOMAS_DISABLE_CLAUDE_MEM_HOOKS`, configured local-only.
- R4 — Document `claude-hud` as per-contributor setup (not committed to
  shared config) pending confirmation that its wizard writes a
  machine-specific path to user-level settings.
- R5 — Record the `claude-context` rejection and the `awesome-claude-code`
  reference-only disposition so neither is re-proposed unaware of the
  reasoning.
- Must remain **additive**: with no `.mcp.json`/hook changes present (older
  Claude Code, `--setting-sources` excluding project settings, CI, any
  non-Claude-Code workflow), `src/mangomas/` behaviour is byte-identical —
  none of this touches a runtime code path.

## Config / env additions

N/A — no `MANGOMAS_*` `Settings` additions; nothing here is read by
`src/mangomas/config.py`.

The actual additions are Claude Code config, not application config:

| Key | File | Default | Purpose |
|---|---|---|---|
| `mcpServers.{filesystem,git,fetch,sequential-thinking,repomix}` | `.mcp.json` | present | Project-scoped MCP server registration |
| `hooks.PreToolUse[].matcher == "Bash"` | `.claude/settings.json` | present | `rtk` output-compaction hook |
| `env.RTK_TELEMETRY_DISABLED` | `.claude/settings.json` | `"1"` | Asserts rtk's telemetry-off stance explicitly |
| `env.MANGOMAS_DISABLE_RTK_HOOK` | `.claude/settings.json` | `"0"` | Per-contributor opt-out for the rtk hook (set `"1"` in a personal `.claude/settings.local.json`) |
| `env.MANGOMAS_DISABLE_CLAUDE_MEM_HOOKS` | `.claude/settings.json` | `"0"` (once Phase 4 lands) | Per-contributor opt-out for claude-mem's hooks |

## Protocol / contract impact

- New/changed protocols: none.
- New error types: none.
- Registry additions: none.

## Backwards-compatibility

- `src/mangomas/` is untouched by every file this spec adds or modifies
  (`.mcp.json`, `.claude/settings.json`, `Makefile`, `.pre-commit-config.yaml`,
  `.github/workflows/ci.yml`, `docs/`, `specs/`, `README.md`, `CLAUDE.md`,
  `tests/tooling/`) — none of them are on any import path the application
  runtime exercises.
- All `.claude/settings.json` edits are additive: the four pre-existing hooks
  (`SessionStart`/`harness_session_start.py`, `PreToolUse`/frontmatter-lint,
  `PostToolUse`/ruff-autofix, `Stop`/pytest) are asserted to survive verbatim
  by `tests/tooling/test_claude_code_settings.py`.

## Test plan

- Unit: `tests/tooling/test_claude_code_settings.py` — parses `.mcp.json` and
  `.claude/settings.json` at runtime (no duplicated-content snapshot) and
  asserts the pre-existing hooks survive, the rtk hook is present and
  opt-out-gated, and the five MCP servers are exactly the adopted set, scoped
  correctly, with no server declaring a secret. Written incrementally: the
  rtk/MCP assertions landed with those changes; claude-mem's assertions are
  added when its hook JSON is known (R3).
- `tests/deploy/test_ci_make_parity.py::test_lint_job_delegates_every_step_to_make`
  updated to include the new `make validate-config` step, keeping CI ↔
  Makefile parity structural rather than a comment asking someone to
  remember.
- Coverage: none of the new code lives under `src/mangomas`, so the
  `--cov=mangomas` denominator and the 95% global / per-package floors are
  unaffected — confirmed by running `make gate` after each change.

## Acceptance criteria

- [x] `.mcp.json` created with 5 pinned MCP servers; `make validate-config`
      (new target, wired into `make gate` and CI's `lint` job) passes.
- [x] `rtk` hook added to `.claude/settings.json`, additive and opt-out-gated;
      `tests/tooling/test_claude_code_settings.py` passes.
- [x] `check-json` added to `.pre-commit-config.yaml`.
- [x] `docs/tooling/claude-code-ecosystem.md`, this spec, and ADR-0020 written.
- [x] `README.md` and `CLAUDE.md` updated.
- [ ] `claude-hud` disposition confirmed by an actual `/claude-hud:setup` run
      (requires a human on a network that can reach GitHub — unreachable from
      this session).
- [ ] `claude-mem` hooks added from an observed `npx claude-mem install` diff,
      local-only provider confirmed selected (requires the user's explicit
      go-ahead to run the installer, plus a network that can reach npm/the
      provider's setup flow).
- [ ] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean —
      confirmed via `make gate` for every phase landed in this session.
- [ ] CHANGELOG updated once the two pending items above land; this spec's
      status moves to Implemented at that point, not before.
