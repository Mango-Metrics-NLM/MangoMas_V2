# ADR-0020: Claude Code ecosystem tooling integration

## Status

Accepted

## Context

Seven external Claude Code ecosystem projects were evaluated for integration
as developer-tooling: `modelcontextprotocol/servers`, `yamadashy/repomix`,
`rtk-ai/rtk`, `thedotmack/claude-mem`, `jarrodwatts/claude-hud`,
`zilliztech/claude-context`, and `hesreallyhim/awesome-claude-code`. None of
these are libraries `src/mangomas/` imports — they configure the Claude Code
environment contributors use *to work on* this repo, via `.mcp.json` and
`.claude/settings.json`, both previously minimal (no MCP servers registered;
four first-party hooks). This repo's Claude Code sessions run in cloud/
Agent-SDK mode, which changes two safety assumptions that would hold for a
human's interactive terminal: project-scoped `.mcp.json` servers load with
**no per-session approval prompt** in this mode, and Claude Code has **no
supported way to disable a single hook** once added to shared config.

## Decision

Adopt `modelcontextprotocol/servers` (filesystem/git/fetch/sequential-
thinking subset), `repomix`, and `rtk` into the shared, checked-in `.mcp.json`
/ `.claude/settings.json`. Wrap `rtk`'s hook command in an env-var-gated
no-op (`MANGOMAS_DISABLE_RTK_HOOK`) since `env` values layer across settings
files even though hook arrays don't — the only real per-contributor opt-out
for a committed hook. Adopt `claude-mem` as a **per-contributor, user-scoped
plugin**: a verified isolated install showed it leaves the project's
`.claude/settings.json` byte-identical and ships its hooks in its own plugin
manifest under `~/.claude/plugins/`, so it takes no shared config and needs
no env gate. Treat `claude-hud` as
per-contributor-only (documented, not committed) pending one hands-on
confirmation that its setup wizard writes a machine-specific path to
user-level settings, as its own docs indicate. Reject `claude-context`.
Treat `awesome-claude-code` as a reference/discovery source, not software.

## Consequences

### Positive

- Contributors get semantic file/git/fetch access, structured reasoning,
  repo-packing, and token-efficient Bash output, wired through the same
  additive/opt-in discipline the rest of the codebase follows; cross-session
  memory is available as a personal add-on.
- The `MANGOMAS_DISABLE_RTK_HOOK` env-var pattern gives individual
  contributors a real opt-out from the one committed hook, despite Claude
  Code's all-or-nothing hook-disable limitation.

### Negative / Trade-offs

- `.mcp.json` and `.claude/settings.json` become a materially larger
  auto-exec/network-reach surface. The 5 adopted MCP servers load with **no
  approval prompt** in this repo's cloud/Agent-SDK execution mode — the
  interactive trust dialog only exists for a human at a terminal. The control
  surface in cloud mode is `disabledMcpjsonServers` (blocks a named server in
  every mode), not the approval flow.
- `claude-mem` reaches no contributor automatically — being user-scoped, it
  is opt-in per machine, so the team gets no shared memory baseline. Whoever
  does install it runs a local background worker (`127.0.0.1:37700`) and a
  `PostToolUse` hook matching `*`, capturing every tool call into local
  storage; cloud sync stays off unless explicitly configured.

### Neutral

- `claude-context` is rejected as redundant with this repo's own RAG stack
  (`src/mangomas/rag/` + `adapters/vector/chroma.py`) and because its default
  path sends code chunks to OpenAI + Zilliz Cloud — a local-only config
  (Ollama + self-hosted Milvus) exists but duplicates infrastructure this
  repo already has for the same purpose. Revisit only if a cross-repo
  semantic-search need arises that the existing single-repo RAG layer can't
  satisfy.

## Alternatives Considered

- **Treat all 7 as personal, undocumented installs** — rejected: the explicit
  request was shared config for most of them, and leaving MCP servers
  undocumented means contributors reinvent ad hoc equivalents.
- **Vendor claude-context with cloud embeddings as the default** — rejected:
  sends this repo's source to third parties by default for capability the
  local RAG stack already provides.

## References

- Code: `.mcp.json`, `.claude/settings.json`, `scripts/lint_agent_frontmatter.py`
- Related ADRs: ADR-0003 (closest precedent for absorbing an external tool
  natively rather than vendoring it), ADR-0002
- Structural precedent: `eval_harness_bridge/` (thin bridge, pinned external
  ref, isolated coverage gate)
- Spec: `specs/0016-claude-code-ecosystem-tooling.md`
- Docs: `docs/tooling/claude-code-ecosystem.md`
