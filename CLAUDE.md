@AGENTS.md

# Mango-Mas V2 — Claude Code Context

The first line above imports [`AGENTS.md`](AGENTS.md), which holds everything
that is not specific to Claude Code: essential commands, architecture, the
design rules, the `MANGOMAS_*` configuration tables, error types, testing
conventions and file ownership. **Read it first — it is the bulk of the
context.** Nothing here repeats it.

What follows is Claude Code's own surface: the agent corpus, the harness and
its hooks, the skill corpus, and the MCP servers.

Measured note on layering: a nested `AGENTS.md` is **not** read while this file
exists (probe matrix in ADR-0035), so per-directory instruction files in this
repository are named `CLAUDE.md`. Nested files load on demand, concatenated
with this one, never overriding it.

---

## Claude Code Agents

27 agents live at `.claude/agents/mango-<slug>.md` — one flat directory, no
hierarchy. Claude Code resolves an agent by its `name:` field, which must equal
the filename stem; the `mango-` prefix separates the committed corpus from
personal agents `/agents` writes into the same directory.

**Four routers.** These carry `Use when:` trigger conditions, so they are what
auto-delegation matches. They read and advise — deliberately no `Edit`, `Write`
or `Bash`.

| Router | Use when |
|--------|----------|
| `mango-architect` | Reviewing a PR, evaluating a design, checking protocol/layering violations, recording an ADR |
| `mango-backend` | Backend work spanning core protocols, adapters, orchestrator, errors, telemetry, workflow or RAG |
| `mango-api-dev` | Adding or changing an endpoint, evolving a request/response schema, API-layer integration |
| `mango-test-engineer` | Adding or fixing tests, diagnosing a coverage gap, choosing a test surface |

**Twenty-three specialists**, invoked *by name*, not by topic match — their
descriptions deliberately carry no trigger conditions, because auto-delegation
matches the condition and never reads a modal verb like "invoke explicitly
when". Name them directly:

| Agent | Owns |
|-------|------|
| `mango-protocol-auditor` / `mango-layering-auditor` | Protocol back-compat / cross-layer import direction (read-only) |
| `mango-adr-author` | ADRs in `docs/adr/` (writes markdown only) |
| `mango-pr-watcher` | PR activity triage (read-only reporter) |
| `mango-llm-adapter-dev` / `mango-storage-adapter-dev` | `adapters/llm/` / `adapters/storage/` |
| `mango-orchestrator-dev` | `core/orchestrator.py` and the whole dispatch surface, incl. `stream_dispatch` |
| `mango-error-taxonomy-dev` | `errors.py` + `api/errors.py::_ERROR_STATUS` |
| `mango-telemetry-exporter-dev` | The OTel exporter seam in `mangomas.telemetry` |
| `mango-workflow-graph-dev` | `workflow/` — graph model, registry, predicates, executor |
| `mango-schema-evolution` / `mango-sse-streamer` | HTTP DTO evolution / API-layer SSE framing |
| `mango-fake-builder` / `mango-hypothesis-fuzz` / `mango-integration-runner` | `tests/fakes.py` / property tests / `tests/integration/` |
| `mango-rag-dev` | `rag/` + the `adapters/embeddings/` and `adapters/vector/` seams |
| `mango-eval-dev` | The `eval/` spine — runner, gates, registries, payload, discovery |
| `mango-secrets-dev` | `secrets/` — protocol, env/GCP backends, strict-mode semantics |
| `mango-agent-impl-dev` | The built-in agents under `agents/` + `_prompt` / `_structured` / discovery + `src/mangomas/cognitive/` producer |
| `mango-cli-dev` | `cli/` — the `main.py` facade, `_app` assembly order, the `_runtime` seam |
| `mango-harness-dev` | `harness/` + the four `scripts/` harness entry points |
| `mango-ci-dev` | `Makefile`, `.github/workflows/`, `dependabot.yml`, `deploy/`, `tests/deploy/` |
| `mango-api-impl-dev` | The FastAPI assembly layer — `create_app` + middleware order, `api/middleware/`, `auth.py`, `health.py`, `tracing.py`, the system/workflow routers, `tenancy.py` |

**Agents vs skills.** They are different things and the tie-break matters:
**skills own procedure** (the recipe for doing X), **agents own a surface** —
its boundary, its invariants, and the shape of its output. An agent reaches for
a skill for the how; a skill never delegates to an agent.

Five agents own a **protected path** (`mango-error-taxonomy-dev`,
`mango-orchestrator-dev`, `mango-schema-evolution`, `mango-hypothesis-fuzz`,
`mango-harness-dev`)
and say so in their bodies: those edits need a `BREAKING-CHANGE` commit
trailer, and the `PreToolUse` hook that warns about it is advisory only.

Write that trailer as `BREAKING-CHANGE: <path> — <rationale>` with the path
**bare and unbackticked, as the first token**, one trailer per path.
`find_marker_scopes` scopes a marker only on an exact match against the
protected set; a backticked path, a typo, or a comma-joined list falls through
to an *unscoped* marker, which approves every touched protected path rather
than the one it names.

To disable agent delegation project-wide, add `Agent` to `permissions.deny` in
`.claude/settings.json`; for yourself only, use your gitignored
`.claude/settings.local.json`.

## Claude Code Harness (opt-in)

The enterprise harness layer is configured by `HarnessSettings` (env prefix
`MANGOMAS_HARNESS__`) and engaged only when `enabled=True`:

| Variable | Default | Purpose |
|---|---|---|
| `MANGOMAS_HARNESS__ENABLED` | `false` | Wrap dispatch + stream_dispatch in `harness.agent_invoke` |
| `MANGOMAS_HARNESS__METRICS_NAMESPACE` | `mangomas.harness` | OTel tracer namespace for harness spans |
| `MANGOMAS_HARNESS__METRICS_EXPORTER` | `inherit` | Harness span exporter (`inherit`/`console`/`gcp`); `inherit` reuses the app exporter |
| `MANGOMAS_HARNESS__HOOK_LOG_LEVEL` | `INFO` | Level for SessionStart-hook log records |
| `MANGOMAS_HARNESS__CONFIG_AUDIT_MODE` | `off` | `ConfigChange` hook mode (`off`/`audit`/`block`) for `.claude/settings.json` / `settings.local.json` edits |
| `MANGOMAS_TELEMETRY__EXPORTER` | `console` | Application span exporter (`console`/`gcp` Cloud Trace) |

When enabled, `composition.build_orchestrator` returns
`_HarnessOrchestrator` instead of the bare `Orchestrator`. The subclass
overrides both `dispatch` and `stream_dispatch` to add a
`harness.agent_invoke` parent span with attributes `agent.name`,
`harness.topology` (`dispatch` or `stream`), and `messages.count`.
`dispatch_pipeline` and `dispatch_fan_out` inherit the wrap because
they delegate through `dispatch`. `stream_dispatch`'s span is opened by a
dedicated `_traced_stream` generator that attaches/detaches OTel context
per chunk (never across a `yield`, which would leak the span into the
consumer's own spans) and closes on full drain, an upstream error, or
early consumer abandonment alike — see ADR-0021.

`src/mangomas/harness/` (`governance.py`, `config_audit.py`) hosts the
in-package governance logic: the protected-path set and `BREAKING-CHANGE`
marker aliases (read from `pyproject.toml`'s `[tool.mangomas.governance]`
table — the single source of truth, shared with `scripts/lint_agent_frontmatter.py`
and `scripts/check_protected_paths.py`), and the `ConfigChange` hook's
decision table.

Protected core paths (`src/mangomas/core/agent.py`,
`src/mangomas/core/orchestrator/`, `src/mangomas/core/structured.py`,
`src/mangomas/core/tools.py`,
`src/mangomas/errors.py`, `src/mangomas/registry.py`) require a
`BREAKING-CHANGE` marker (the legacy `# approved-breaking-change` form is
still accepted) on at least one commit message when touched. Since ADR-0030
the **governance mechanism itself** is protected on the same terms —
`pyproject.toml`, `scripts/check_protected_paths.py`, `scripts/_governance.py`,
`src/mangomas/harness/governance.py`, `sitecustomize.py` and `.mcp.json`
(named by `harness.governance.GOVERNANCE_SURFACE`) — and a marker may name the
path it approves: `BREAKING-CHANGE: <path> — <rationale>` binds to that file,
while a bare `BREAKING-CHANGE: <rationale>` still approves every touched path.
The gate reads the policy from the **base ref**, not the branch under test, so
a branch cannot shrink the set it is judged by. The
**authoritative** enforcement is `scripts/check_protected_paths.py`, a CI
job (`make protected-paths`) that reads `git diff`/`git log` between the PR
base and head — state an in-session agent cannot rewrite. The `PreToolUse`
hook (below) is **advisory only**: it cannot be a complete gate regardless
of internal correctness, since `Bash`/MCP filesystem tool calls bypass its
`Edit|Write|NotebookEdit` matcher entirely.

Four scripts in `scripts/` complete the harness:

- `lint_agent_frontmatter.py` — Pydantic-validated lint of
  `.claude/agents/mango-*.md` / `.claude/skills/*/SKILL.md` frontmatter
  (default, no-flag mode; wired into CI as the `Frontmatter lint` step of the
  `lint` job, invoked via `make frontmatter`). Rejects the Copilot agent
  format field by field — `argument-hint`/`sub_agents` as wrong-here, and
  `permissionMode`/`hooks` by a policy table rather than `extra="forbid"`,
  which would report "Extra inputs are not permitted" for a field Claude Code
  genuinely accepts. `tools` is **required**: omitting it makes an agent
  inherit every tool. Each glob must match at least
  `MIN_AGENT_FILES` / `MIN_SKILL_FILES` files (overridable via
  `--min-agents` / `--min-skills`, which reject values below
  `MIN_FLOOR_LOWER_BOUND` — a floor of 0 or less can never fail): a glob
  matching zero files used to fall through to `EXIT_OK`, so a corpus that
  moved produced a green gate that validated nothing. Also serves two
  **stdlib-only** hook
  modes reading Claude Code's tool-call JSON from stdin (never a
  `$CLAUDE_TOOL_INPUT_*` env var — Claude Code does not define one):
  `--hook pre-tool-use` emits an advisory `permissionDecision: "ask"` for a
  protected-path edit, and `--hook post-tool-use --emit-path` prints the
  edited file's path for piping into `ruff check --fix`. Neither mode
  requires `pydantic`/`pyyaml` to be installed. The legacy
  `--check-protected-paths <path>` flag (staged-diff based) remains for
  pre-commit, where a staged diff genuinely exists.
- `check_protected_paths.py` — the CI gate described above.
- `harness_config_audit.py` — `ConfigChange` hook; evaluates
  `mangomas.harness.config_audit.evaluate_config_change` against
  `HarnessSettings.config_audit_mode`, deferring its `mangomas.config`/
  `mangomas.telemetry` imports so it degrades to the default mode rather
  than crashing when `mangomas` isn't installed.
- `harness_session_start.py` — SessionStart hook for Claude Code on
  the web. Emits a single-line JSON probe report (venv + LM Studio)
  so a fresh session knows what's available. Always returns `EXIT_OK`.

## Claude Code Skills

Skills are workflow-scoped helpers under `.claude/skills/<name>/SKILL.md`
(spec-0018 / ADR-0024). Claude Code loads only each skill's `description` at
startup and the body on first use, so the corpus costs almost nothing until a
skill is actually invoked. VS Code Copilot reads the same directory, so one
tree serves both.

| Skill | Use when |
|-------|----------|
| `mango-testing` | Writing/running tests, extending fakes, coverage |
| `mango-adapter` | Adding a new LLM/storage/memory/secrets adapter |
| `mango-agent-add` | Adding a new agent following the 4-step pattern |
| `mango-error` | Adding a new error type with HTTP mapping |
| `mango-observability` | Instrumenting with spans + structured logging |
| `mango-config` | Adding a new tunable to `Settings` |
| `mango-topology` | Composing pipelines, fan-outs, acceptance loops (imperative) |
| `mango-workflow` | Declarative workflow graphs: schema, nodes, predicates, `workflow` CLI |
| `mango-rag` | Embeddings/vector/RAG: ingestion, retrieval, RetrievalTool wiring |
| `mango-eval` | Evaluation harness: scorers, sinks, targets, sources, gate/baseline |
| `mango-cognitive` | CognitiveSignal 1.1.0 producer: `MANGOMAS_SIGNAL__*`, extras sink, INV-16 |
| `mango-harness` | Protected-path governance, the `BREAKING-CHANGE` trailer, hooks |
| `mango-deploy` | Cloud Run deploy + telemetry-exporter selection (GCP swap) |
| `mango-release` | Drafting CHANGELOG, PR description, pre-merge checklist |
| `mango-mutation-proof` | Proving a guard fails when the thing it guards breaks |
| `mango-coverage-audit` | Checking a coverage number is measured over the right denominator |
| `mango-decompose` | Splitting a god file into a package: extract, facade, wire into `test_import_compat.py`, verify per-submodule coverage |

---

## Claude Code MCP Servers & Ecosystem Tooling

Project-scoped MCP servers are declared in `.mcp.json` (repo root). In a
human's interactive terminal session they connect after a one-time
workspace-trust approval (`claude mcp list` to check status; `/mcp` to
approve pending servers); **cloud/Agent-SDK sessions skip that prompt
entirely and load them with no approval step** — see
`docs/tooling/claude-code-ecosystem.md` before assuming otherwise.

| Server | Use for |
|--------|---------|
| `filesystem` | Reading/listing files scoped to the repo root |
| `git` | `git log`/`blame`/`diff` introspection via MCP instead of Bash |
| `fetch` | Retrieving a URL's content directly (e.g. upstream library docs) |
| `sequential-thinking` | Structured multi-step reasoning for planning-heavy tasks |
| `repomix` | Pack a directory into one context-efficient bundle before a cross-cutting refactor |
| `github` | PR/issue/CI reads via the official server. **Optional** — needs docker and a `GITHUB_PERSONAL_ACCESS_TOKEN`; without them it fails to start and the other five are unaffected |

`rtk` (if installed locally) transparently compacts noisy Bash stdout via a
`PreToolUse` hook; the hook guards on `command -v rtk`, so if the binary is
absent it is skipped silently and Bash tool calls work exactly as before
(see ADR-0020). A contributor can opt out individually
with `MANGOMAS_DISABLE_RTK_HOOK=1` in their personal
`.claude/settings.local.json`, since Claude Code has no per-hook disable.

Cross-session memory (`claude-mem`) and the `claude-hud` statusline are
**per-contributor, user-scoped installs** — neither appears in this repo's
config and neither is required to work here. `claude-mem` was verified to
leave the project's `.claude/settings.json` byte-identical (it ships its
hooks as a Claude Code plugin under `~/.claude/plugins/`); `claude-hud`'s
disposition is still pending one hands-on `/claude-hud:setup` run. See
`docs/tooling/claude-code-ecosystem.md`. `zilliztech/claude-context` was evaluated and
explicitly **rejected** (redundant with this repo's own `rag/` + Chroma
stack; sends code to third parties by default) — see ADR-0020 before
re-proposing it.

---

