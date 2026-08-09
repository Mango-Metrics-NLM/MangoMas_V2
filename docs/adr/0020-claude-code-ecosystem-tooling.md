# ADR-0020: Claude Code ecosystem tooling integration

## Status

Accepted

## Context

Seven external Claude Code ecosystem projects were evaluated as developer
tooling: `modelcontextprotocol/servers`, `repomix`, `rtk`, `claude-mem`,
`claude-hud`, `claude-context`, and `awesome-claude-code`. None are libraries
`src/mangomas/` imports — they configure the Claude Code environment used *to
work on* this repo, via `.mcp.json` and `.claude/settings.json`. Two platform
facts drive the decision, both confirmed against Claude Code's docs: in this
repo's cloud/Agent-SDK sessions, project `.mcp.json` servers load with **no
approval prompt**, and there is **no supported way to disable a single hook**
once one is committed.

## Decision

Adopt into shared config: the `filesystem`/`git`/`fetch`/`sequential-thinking`
reference servers plus `repomix` (`.mcp.json`), and `rtk` (one `PreToolUse`
hook). Gate `rtk`'s hook on `MANGOMAS_DISABLE_RTK_HOOK` — `env` values layer
across settings files even though hook arrays don't, making this the only
real per-contributor opt-out for a committed hook.

Adopt `claude-mem` and `claude-hud` as **per-contributor, user-scoped
installs**, not shared config. For `claude-mem` this is verified: an isolated
install left the project's `.claude/settings.json` byte-identical and placed
its hooks in the plugin's own manifest under `~/.claude/plugins/`. For
`claude-hud` it is the documented behaviour, pending one hands-on
confirmation.

Reject `claude-context`. Treat `awesome-claude-code` as a discovery source,
not software.

## Consequences

### Positive

- Contributors get scoped file/git/fetch access, structured reasoning,
  repo-packing, and token-efficient Bash output through the repo's usual
  additive/opt-in discipline; memory and statusline remain personal add-ons.

### Negative / Trade-offs

- `.mcp.json` / `.claude/settings.json` become a materially larger auto-exec
  and network-reach surface, and in cloud mode the 5 servers load unprompted.
  The control lever there is `disabledMcpjsonServers`, not the trust dialog.
- Nothing gives the team a shared memory baseline, since `claude-mem` is
  per-machine.
- `claude-mem`, where installed, captures **every** tool call's output to
  `~/.claude-mem/` — output that routinely carries secrets, in a store
  outside the repo that `gitleaks` and `.gitignore` don't cover. Its
  local-only posture is a default, not an invariant: `CLAUDE_MEM_CLOUD_SYNC_HUB_URL`
  and `CLAUDE_MEM_CHROMA_HOST`/`_API_KEY` turn that capture into outbound
  data once set. See the tooling doc before opting in.

### Neutral

- `claude-context` is redundant with this repo's own RAG stack
  (`src/mangomas/rag/` + `adapters/vector/chroma.py`), and its default path
  ships code chunks to OpenAI + Zilliz Cloud. A fully local config exists but
  duplicates infrastructure already here. Revisit only for a cross-repo
  semantic-search need the current single-repo layer can't serve.

## Alternatives Considered

- **All seven as personal, undocumented installs** — rejected: leaves MCP
  servers undocumented, so contributors reinvent ad hoc equivalents.
- **Vendor `claude-context` with cloud embeddings by default** — rejected:
  sends this repo's source to third parties for capability already present.

## References

- Code: `.mcp.json`, `.claude/settings.json`
- Related ADRs: ADR-0003 (precedent for absorbing an external tool natively
  rather than vendoring it), ADR-0021 (protected-path governance)
- Spec: `specs/0016-claude-code-ecosystem-tooling.md`
- Docs: `docs/tooling/claude-code-ecosystem.md`
