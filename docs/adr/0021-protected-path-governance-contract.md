# ADR-0021: Protected-path governance — CI trailer gate, PreToolUse advisory

## Status

Accepted

## Context

CLAUDE.md documents five files as protected core contracts requiring a
`BREAKING-CHANGE` marker on any edit. The only implementation of that rule was a
`PreToolUse` hook reading `$CLAUDE_TOOL_INPUT_path` — an environment variable Claude
Code does not define, since hook input arrives as JSON on stdin. The check has
therefore always resolved to "no path" → not protected → allow. Independently, the
script crashes on `import pydantic` in a bare interpreter and exits 1 (non-blocking).
There is no other enforcement anywhere in the repo: not in `make gate`, not in CI, not
in pre-commit.

Even a corrected stdin-reading version cannot be a complete *blocking* gate at the
`PreToolUse` layer: `git diff --staged` is empty at edit time (nothing is staged yet),
so a hook that blocks on "no marker in staged diff" would block every protected edit,
not just unmarked ones. And the `Edit|Write` matcher is exact-match, so it does not
cover `Bash` or MCP filesystem tool calls, which this repo also grants (the `filesystem`
MCP server is project-scoped in `.mcp.json`). No amount of hook-side fixing closes that
gap, because the agent being gated controls what happens inside its own `Bash` calls.

## Decision

The authoritative enforcement point moves to a CI job that computes the diff and commit
trailers between the PR base and head — state an in-session agent cannot rewrite,
because it cannot edit branch protection. The `PreToolUse` hook is corrected to read
stdin JSON (fixing the crash and the dead env-var read) but is demoted to an advisory
`permissionDecision: "ask"`, which routes the decision to the human reviewing the tool
call rather than attempting an unenforceable in-session block.

## Consequences

### Positive

- The gate is enforced by something the agent cannot bypass by writing a file or
  running a shell command.
- The PreToolUse hook now actually runs (no crash, no dead env var) and gives the human
  a heads-up before approving, which is strictly better than today's silent no-op.
- `PROTECTED_PATHS` has one definition (`pyproject.toml`), read via stdlib `tomllib` by
  both the CI script and the hook, so the hook never needs `mangomas` installed.

### Negative / Trade-offs

- The gate no longer *blocks* an in-session edit; a protected file can still be edited
  and staged locally. The block happens at the CI boundary, one step later than before
  (where "before" means: never, since the old block didn't work).
- `Bash`/MCP filesystem writes to protected paths still are not advisory-flagged in
  `PreToolUse` — only `Edit`/`Write`/`NotebookEdit` are. This is a known, accepted gap;
  the CI gate is the actual backstop for every write surface.

### Neutral

- The legacy `--check-protected-paths <path>` flag (staged-diff based) is retained
  unchanged for pre-commit, where a staged diff genuinely exists.

## Alternatives Considered

- **Fix the stdin bug and keep PreToolUse as a hard block** — rejected: an agent with
  `Write` can create any proposed ack file itself (the ack file is not protected), and
  `Bash`/MCP writes bypass the matcher entirely, so a hard block at this layer is
  performative rather than real.
- **Ack-file / env-var precedence ladder** — rejected for the same reason: both are
  writable by the agent being gated, so neither is a real approval signal.

## References

- Code: `scripts/lint_agent_frontmatter.py`, `scripts/check_protected_paths.py`,
  `.claude/settings.json`, `pyproject.toml` `[tool.mangomas.governance]`
- Related ADRs: ADR-0022 (harness governance port), supersedes `main`'s ADR-0011
  (harness-hook-hardening, different decision on that branch — see spec-0017)
