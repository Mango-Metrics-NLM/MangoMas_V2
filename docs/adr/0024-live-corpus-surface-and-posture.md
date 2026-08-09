# ADR-0024: Live corpus — surface choice and permission posture

## Status

Accepted

## Context

The repo's 19 agents and 12 skills are valid, working **GitHub Copilot** artefacts
(`.github/agents/**/*.agent.md`, `.github/skills/<name>/SKILL.md`) — `read`/`edit`/`search`/
`execute` are documented Copilot tool aliases, not invented ones. Claude Code reads nothing
from `.github/`, so the corpus is inert for the tool this project actually uses. VS Code
Copilot also scans `.claude/agents/` and `.claude/skills/`, and neither tool discovers
Copilot's nested `<parent>/<child>` layout, which hides 15 of the 19 agents from Copilot
today.

Making the corpus live is a **capability change**, not a filing change: subagents are
auto-delegated from their `description` alone, run in the background by default, and
inherit *every* tool — including `Bash`, `Edit`, `Write` — when `tools:` is omitted. There
is no subagent equivalent of a skill's `disable-model-invocation`, so descriptions are the
only routing control available.

## Decision

Move the corpus to `.claude/` as the single home, serving Claude Code and VS Code Copilot
and giving up only the github.com cloud-agent surface. Every agent declares `tools`
explicitly; coordinators may read and delegate but not write; a **path-scoped**
`permissions.deny` protects the governed paths; and skills own procedure while agents carry
only ownership, routing boundary, and output format.

## Consequences

### Positive

- The corpus loads for the first time in Claude Code, and for the first time reaches the 15
  nested agents in VS Code Copilot.
- Least privilege is enforced where Claude Code actually reads it (`tools:`), not in prose.
- Path-scoped denial covers the gap `tools:` cannot express, and that `Bash` opens by
  bypassing the `PreToolUse` `Edit|Write|NotebookEdit` matcher entirely.
- One corpus, one format — no generator, no rename table, no second source of truth.

### Negative / Trade-offs

- The github.com Copilot cloud-agent surface is given up. Recorded in CHANGELOG under
  `Changed`, since the capability moves rather than disappears.
- `Bash` on implementers is a real grant. `permissions.allow` narrows it, but honestly:
  `Bash(python -m pytest:*)` combined with `Write` under `tests/` is an execution path, and
  the `Stop` hook runs pytest every turn. It is mitigated by the path deny and by human diff
  review — **not** by the allow-list, which this ADR declines to over-claim.
- Auto-delegation cannot be disabled per agent. The 11 implementers are therefore phrased
  for explicit invocation, which is a convention, not an enforced control.

### Neutral

- `permissionMode` and `hooks` are **valid** Claude Code fields, rejected here as project
  policy: a committed file setting `permissionMode: bypassPermissions` would raise the
  privilege of any session that delegates to it, and a per-agent hook executes shell outside
  the reviewed `.claude/settings.json` set. The linter's message says exactly that, rather
  than implying the fields are unrecognised.

## Alternatives Considered

- **Keep both trees in sync** — rejected: two sources of truth, and the formats genuinely
  differ (`agents:` vs `tools: Agent(...)`), so a generator's failure mode is silent
  privilege drift.
- **`Agent(name)` staged-rollout deny-list** — rejected: it has an end state nobody reaches,
  and a permanently-denied agent *looks* live while being dead, which is precisely the
  pathology this work exists to remove. Path-scoped denial has no end state and cannot rot.
- **`extra="allow"` + a deny-list on the schema** — rejected: a typo'd `toolz:` would pass
  silently and, because omitting `tools` inherits everything, yield a fully-privileged agent.
  `extra="forbid"` fails loudly and boundedly instead.
- **Curating to ~7 agents** — rejected: the argument rested on agent bodies duplicating
  `CLAUDE.md`, measured at 6–16 %, not the claimed 50–60 %. The real redundancy is
  agent↔skill, addressed by the ownership rule instead of by deletion.

## References

- Code: `scripts/lint_agent_frontmatter.py`, `.claude/settings.json`,
  `pyproject.toml` `[tool.mangomas.governance]`
- Spec: `specs/0018-live-claude-code-corpus.md`
- Related ADRs: ADR-0020 (ecosystem tooling), ADR-0021 (protected-path governance)
- External: Claude Code sub-agents and skills docs; GitHub Copilot custom-agents and
  agent-skills configuration references
