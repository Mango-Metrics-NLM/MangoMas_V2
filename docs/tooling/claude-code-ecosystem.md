# Claude Code Ecosystem Tooling

Seven external Claude Code ecosystem projects were evaluated as developer
tooling for contributors working on this repo. None of them are
`src/mangomas/` dependencies — they configure the Claude Code environment
itself, via `.mcp.json` and `.claude/settings.json`. See
[ADR-0020](../adr/0020-claude-code-ecosystem-tooling.md) and
[spec-0016](../../specs/0016-claude-code-ecosystem-tooling.md) for the full
decision record.

| Tool | Disposition |
|---|---|
| `modelcontextprotocol/servers` (filesystem/git/fetch/sequential-thinking) | Adopted, shared (`.mcp.json`) |
| `yamadashy/repomix` | Adopted, shared (`.mcp.json`) |
| `rtk-ai/rtk` | Adopted, shared (`.claude/settings.json` hook), opt-out available |
| `thedotmack/claude-mem` | Adopted, shared (`.claude/settings.json` hooks), opt-out available — **pending** (see below) |
| `jarrodwatts/claude-hud` | Per-contributor only — **pending confirmation** (see below) |
| `zilliztech/claude-context` | Rejected — see [ADR-0020](../adr/0020-claude-code-ecosystem-tooling.md) |
| `hesreallyhim/awesome-claude-code` | Reference/discovery source, not software |

## Prerequisites

- **Node.js 20+ (LTS)** — the MCP servers and `repomix` are `npx`-invoked;
  nothing is vendored into this repo (no `package.json`, no lockfile).
- **`uv`** (for `uvx`) — the `git` and `fetch` MCP servers are Python-based
  reference implementations, run via `uvx` rather than a local install.
- Python/pip prerequisites are unchanged from the main [README](../../README.md).

## MCP servers (`.mcp.json`)

Five servers, none requiring an API key, `filesystem`/`git` scoped to
`${CLAUDE_PROJECT_DIR:-.}` so they can't reach outside this repo:

| Server | Use for |
|---|---|
| `filesystem` | Reading/listing files scoped to the repo root |
| `git` | `git log`/`blame`/`diff` introspection via MCP instead of Bash |
| `fetch` | Retrieving a URL's content directly (e.g. upstream library docs) |
| `sequential-thinking` | Structured multi-step reasoning for planning-heavy tasks |
| `repomix` | Pack a directory into one context-efficient bundle before a cross-cutting refactor |

Deliberately **not** included from the upstream `modelcontextprotocol/servers`
set: `memory` (redundant with `claude-mem`, below), `everything` (an
MCP-client test/demo server), `time` (low value for this project). Not
re-proposing these without reading this note first.

**Cloud/Agent-SDK sessions get no approval prompt.** Claude Code's
interactive trust dialog for project-scoped `.mcp.json` servers only exists
for a human running `claude` at a terminal — `claude -p`, Agent SDK sessions,
and cloud sessions load these 5 servers with no per-session approval step at
all. If one ever needs to be pulled without waiting for a settings-file edit
to propagate everywhere, the control surface is `disabledMcpjsonServers`
(blocks a named server in every mode), not the approval flow.

## `rtk` — Bash output compaction

Wired as a `PreToolUse`/`Bash` hook in `.claude/settings.json`. The hook
guards on `command -v rtk`, so if the binary isn't installed it is skipped
silently and Bash tool calls behave exactly as before — no per-call
hook-error noise. Claude Code's `PreToolUse` contract (only exit code 2
blocks) is the backstop beneath that. A genuine `rtk` failure — installed but
erroring — is deliberately *not* swallowed, so it stays visible rather than
being masked by a blanket `|| true`.

- **Install:** `brew install rtk` (macOS/Linux). No winget/scoop package for
  Windows — download the release zip, extract, and add to `PATH` manually;
  run from Command Prompt/PowerShell/Windows Terminal, not by double-clicking
  the `.exe`.
- **Telemetry:** off by default upstream; asserted explicitly via
  `RTK_TELEMETRY_DISABLED=1` in the shared `env` block regardless.
- **Opt out individually:** Claude Code has no way to disable a single hook
  short of `disableAllHooks` (which would also kill this repo's frontmatter
  lint, ruff autofix, and pytest-on-stop hooks). Set
  `MANGOMAS_DISABLE_RTK_HOOK=1` in your personal, gitignored
  `.claude/settings.local.json` `env` block instead — `env` values layer
  across settings files even though hook arrays don't.

## `claude-mem` — cross-session memory (pending)

**Not yet wired in.** Per spec-0016/ADR-0020, `claude-mem`'s hook JSON must
come from an observed `npx claude-mem install` diff, never hand-authored —
its installer determines the exact commands for its `SessionStart`/
`UserPromptSubmit`/`PostToolUse`/`Stop`/`SessionEnd` hooks. Running that
installer executes third-party code and needs the user's explicit go-ahead
first (a disposable container bounds the blast radius; it doesn't substitute
for authorization). Once run:

1. Confirm the diff only *appends* to the existing hook arrays.
2. During setup, explicitly select the **local-only** memory provider —
   decline the "CMEM Pro" hosted-tier default-nudge.
3. Confirm `~/.claude-mem/` (SQLite + local Chroma) has no cloud-sync URL set.
4. Wrap each hook command in the same `MANGOMAS_DISABLE_CLAUDE_MEM_HOOKS`
   opt-out pattern as `rtk` above before committing.

`claude-mem`'s memory is entirely separate from this repo's own memory
systems (`MANGOMAS_MEMORY__ENABLED` file-memory, and the RAG/vector layer at
`src/mangomas/adapters/vector/chroma.py`) — it's memory *of the developer's
coding sessions* under `~/.claude-mem/`, not application runtime state.

## `claude-hud` — statusline (pending, per-contributor only)

**Not committed to shared config.** `claude-hud`'s setup wizard is
documented as writing a machine-specific absolute binary path to
**user-level** `~/.claude/settings.json`, not this repo's — there's no
portable value to check in. That's the working assumption pending one
hands-on confirmation (see spec-0016's acceptance criteria); until then,
install it yourself if you want it:

```
/plugin marketplace add jarrodwatts/claude-hud
/plugin install claude-hud
/claude-hud:setup
```

**Known issue:** upstream issue #121 reports `/claude-hud:setup` generating
a broken command on Windows. Since this repo's primary documented dev
environment is PowerShell, re-test at install time in case it's since been
fixed.

## Rejected: `claude-context`

Semantic code search MCP server, redundant with this repo's own RAG stack
(`src/mangomas/rag/` + `adapters/vector/chroma.py`), and its default path
sends code chunks to OpenAI (embeddings) + Zilliz Cloud (vector index) —
third-party data egress for capability this repo already has locally. See
[ADR-0020](../adr/0020-claude-code-ecosystem-tooling.md) for the full
rationale before re-proposing it.

## Reference only: `awesome-claude-code`

A curated, actively-maintained link list — not installable software. Useful
as a periodic-review discovery source (Skills, Status Lines, Memory &
Context Persistence, Observability & Monitoring, Security categories are the
most relevant to this repo's own `.claude/`/`.github/agents`/`.github/skills`
surface), but every entry needs independent vetting before adoption; the
list itself vouches for nothing.

## Verification

**Agent-automatable:**

```bash
make validate-config          # .mcp.json / .claude/settings.json JSON syntax
python -m pytest tests/tooling/ --no-cov
make gate                     # full pre-PR gate, unaffected src/mangomas counts
```

**Requires a human at an interactive terminal with unrestricted network
access** (this repo's sandboxed CI/agent sessions can reach `registry.npmjs.org`/
`pypi.org` but not `github.com`/`api.github.com`/`formulae.brew.sh`):

```bash
claude mcp list                                # all 5 servers connect
claude mcp get filesystem                       # ${CLAUDE_PROJECT_DIR:-.} resolved, not literal
MANGOMAS_DISABLE_RTK_HOOK=1 <run a Bash tool call>   # rtk goes quiet, other hooks still fire
```

Plus one functional smoke test per MCP server (list the repo root via
`filesystem`, `git log` via `git`, pack a small subdirectory via `repomix`),
and — once installed — a positive-path check that `rtk` actually rewrites
Bash output rather than only the negative "absent → passes through
unmodified" path.
