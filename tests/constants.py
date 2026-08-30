"""Shared test constants.

Import these instead of repeating magic literals in tests.
Defaults that mirror ``mangomas.config`` are re-exported from it below so a
config change can never silently desync the tests.
"""

from __future__ import annotations

# The CLI's three exit codes are re-exported the same way. `cli.exit_codes` is a
# pure-constant leaf importing only `typing`, so this costs nothing at import
# time — unlike `cli.main`, which would pull the whole command tree and the four
# eval-registry side-effect imports into every module that reads a constant.
from mangomas.cli.exit_codes import EVAL_GATE_EXIT_CODE as EVAL_GATE_EXIT_CODE
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR as EXIT_CONFIG_ERROR
from mangomas.cli.exit_codes import EXIT_RUNTIME_ERROR as EXIT_RUNTIME_ERROR

# Defaults that mirror ``mangomas.config`` are re-exported (``X as X``) rather
# than restated, so the config remains the single source of truth.
from mangomas.config import (
    DEFAULT_EMBEDDINGS_BATCH_SIZE as DEFAULT_EMBEDDINGS_BATCH_SIZE,
)
from mangomas.config import (
    DEFAULT_EMBEDDINGS_MODEL as DEFAULT_EMBEDDINGS_MODEL,
)
from mangomas.config import (
    DEFAULT_EMBEDDINGS_PROVIDER as DEFAULT_EMBEDDINGS_PROVIDER,
)
from mangomas.config import (
    DEFAULT_LLM_BASE_URL as DEFAULT_LLM_BASE_URL,
)
from mangomas.config import (
    DEFAULT_LOOP_MAX_STEPS as DEFAULT_LOOP_MAX_STEPS,
)
from mangomas.config import (
    DEFAULT_LOOP_STEP_TIMEOUT as DEFAULT_LOOP_STEP_TIMEOUT,
)
from mangomas.config import (
    DEFAULT_RAG_CHUNK_OVERLAP as DEFAULT_RAG_CHUNK_OVERLAP,
)
from mangomas.config import (
    DEFAULT_RAG_CHUNK_WORDS as DEFAULT_RAG_CHUNK_WORDS,
)
from mangomas.config import (
    DEFAULT_RAG_MIN_CHUNK_WORDS as DEFAULT_RAG_MIN_CHUNK_WORDS,
)
from mangomas.config import (
    DEFAULT_SUMMARIZE_HISTORY_LIMIT as DEFAULT_SUMMARIZE_HISTORY_LIMIT,
)
from mangomas.config import (
    DEFAULT_VECTOR_COLLECTION as DEFAULT_VECTOR_COLLECTION,
)
from mangomas.config import (
    DEFAULT_VECTOR_PERSIST_DIR as DEFAULT_VECTOR_PERSIST_DIR,
)
from mangomas.config import (
    DEFAULT_VECTOR_PROVIDER as DEFAULT_VECTOR_PROVIDER,
)
from mangomas.config import (
    DEFAULT_VECTOR_TOP_K as DEFAULT_VECTOR_TOP_K,
)

# ── LM Studio E2E env-var names (single source of truth) ──────────────────────
LMSTUDIO_BASE_URL_ENV: str = "LMSTUDIO_BASE_URL"
LMSTUDIO_MODEL_ENV: str = "LMSTUDIO_MODEL"

# ── Vertex AI E2E env-var names (single source of truth) ──────────────────────
VERTEX_PROJECT_ENV: str = "VERTEX_PROJECT_ID"
VERTEX_LOCATION_ENV: str = "VERTEX_LOCATION"
VERTEX_MODEL_ENV: str = "VERTEX_MODEL"
VERTEX_CREDENTIALS_PATH_ENV: str = "VERTEX_CREDENTIALS_PATH"
# Default Vertex model used by E2E tests when ``VERTEX_MODEL`` is unset.
DEFAULT_VERTEX_TEST_MODEL: str = "gemini-1.5-flash"
STUB_VERTEX_REPLY: str = "stub-vertex-reply"

# ── HTTP client timeouts (test-scoped) ────────────────────────────────────────
# Per-request timeout for httpx.AsyncClient calls in E2E tests. The underlying
# LMStudioClient timeout is configured separately via
# ``LMSTUDIO_E2E_TIMEOUT_SECONDS`` in ``tests/lmstudio/conftest.py``; this
# value bounds how long the test itself waits for the ASGI roundtrip to
# return (which for LM Studio scenarios is effectively bounded by the LLM
# client's timeout anyway).
HTTPX_REQUEST_TIMEOUT_SECONDS: float = 60.0
# Tighter timeout for tests that *expect* a fast failure response and should
# not be patient about hanging requests.
HTTPX_ERROR_PATH_TIMEOUT_SECONDS: float = 30.0

# ── ASGI test transport base URL ──────────────────────────────────────────────
# httpx idiom for in-process ASGI testing — not a real server.
ASGI_TEST_BASE_URL: str = "http://testserver"

# ── In-process mock LM Studio base URL ───────────────────────────────────────
# Used by respx-mocked unit tests in ``tests/test_lmstudio.py`` — the value
# does not need to resolve; respx intercepts on hostname match. Kept distinct
# from ``ASGI_TEST_BASE_URL`` so a future refactor that shares the ASGI URL
# does not accidentally redirect mocked LLM traffic.
TEST_LMSTUDIO_MOCK_BASE_URL: str = "http://lm/v1"
TEST_LMSTUDIO_MOCK_MODEL: str = "m"

# ── Embeddings mock ───────────────────────────────────────────────────────────
TEST_EMBEDDINGS_MOCK_MODEL: str = "embed-m"
# Gated live embedding smoke tests (spec 0013): the loaded embedding model id.
LMSTUDIO_EMBEDDING_MODEL_ENV: str = "LMSTUDIO_EMBEDDING_MODEL"
VERTEX_EMBEDDING_MODEL_ENV: str = "VERTEX_EMBEDDING_MODEL"
DEFAULT_VERTEX_EMBEDDING_MODEL: str = "text-embedding-004"

# Stand-in GCP project id for Vertex adapter unit tests (never contacts GCP).
TEST_VERTEX_PROJECT: str = "test-project"

# ── Oversized upstream-body fixture (spec 0014 / D2) ──────────────────────────
# Comfortably larger than ``DEFAULT_ERROR_DETAIL_TRUNCATE`` (200) so the
# truncation of client-visible error detail is observable in regression tests.
LARGE_UPSTREAM_BODY_CHARS: int = 5000

# ── Vector store test-scoped values ───────────────────────────────────────────
TEST_VECTOR_PERSIST_DIR: str = "./data/test-chroma"
TEST_VECTOR_COLLECTION: str = "test-col"

# ── Agent / reply stubs ───────────────────────────────────────────────────────
DEFAULT_AGENT_NAME: str = "chat"
STUB_REPLY: str = "stub-reply"

# ── Tool stubs ────────────────────────────────────────────────────────────────
DEFAULT_TOOL_NAME: str = "echo"
DEFAULT_TOOL_RESULT: str = "echo-result"
# ToolAgent LLM-call budget values used by tests (distinct from the config
# default so overrides are observable).
TEST_TOOL_MAX_STEPS: int = 2
TEST_TOOL_MAX_STEPS_OVERRIDE: int = 3
# Custom system prompt for prompt-combination tests.
TEST_TOOL_SYSTEM_PROMPT: str = "Answer like a pirate."
# Env var driving AgentSettings.max_tool_steps for the "tool" agent.
TOOL_MAX_STEPS_ENV: str = "MANGOMAS_AGENTS__TOOL__MAX_TOOL_STEPS"

# ── SummarizeAgent history window ─────────────────────────────────────────────
# Turn-window values used by tests (distinct from the config default so an
# override is observable), mirroring the TEST_TOOL_MAX_STEPS pair above.
TEST_HISTORY_LIMIT: int = 2
TEST_HISTORY_LIMIT_OVERRIDE: int = 3
# Number of turns seeded before asserting the window actually caps the fetch.
TEST_HISTORY_SEEDED_TURNS: int = 5
# Env var driving AgentSettings.history_limit for the "summarize" agent.
SUMMARIZE_HISTORY_LIMIT_ENV: str = "MANGOMAS_AGENTS__SUMMARIZE__HISTORY_LIMIT"

# ── Harness frontmatter linter fixtures ───────────────────────────────────────
VALID_AGENT_FRONTMATTER: str = """\
---
name: Example
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
tools: [read, search]
model: Claude Sonnet 4.5 (copilot)
argument-hint: "Pass an example argument"
---

Body content.
"""

# Claude Code agent format (spec-0018 / ADR-0024). Kept beside the legacy
# Copilot fixture above rather than replacing it: the migration needs both, so
# tests can assert the new format passes *and* that the old one is rejected with
# a message naming the specific field rather than a generic "extra inputs".
VALID_CLAUDE_AGENT_FRONTMATTER: str = """\
---
name: example-agent
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
tools: Read, Grep, Glob, Skill
model: inherit
---
"""

# Field values that must be rejected, each standing for a real defect in the
# migrating corpus. Kept as data so a new rejection rule adds a row, not a test.
INVALID_AGENT_MODEL_VALUES: tuple[str, ...] = (
    "Claude Sonnet 4.5 (copilot)",  # every one of the 19 agents carried this
    "Opus",
    "claude opus 5",
)
# Copilot tool aliases: valid in that tool, meaningless to Claude Code — and
# because omitting `tools` inherits everything, an unrecognised list is the
# dangerous kind of wrong rather than a harmless one.
INVALID_AGENT_TOOL_TOKENS: tuple[str, ...] = ("read", "edit", "search", "execute")
VALID_AGENT_TOOL_TOKENS: tuple[str, ...] = ("Read", "Grep", "Glob", "Skill", "Edit", "Write")
# An MCP tool name cannot be enumerated ahead of time — it depends on the
# caller's .mcp.json — so the validator accepts the shape.
VALID_MCP_TOOL_NAME: str = "mcp__github__pull_request_read"
# Delegation scoping that Claude Code ignores inside a subagent definition: the
# agent gets unrestricted delegation, not the named subset.
SCOPED_DELEGATION_TOOL_SPEC: str = "Agent(protocol-auditor, layering-auditor)"
# Fields Claude Code accepts and this project declines, vs fields carried over
# from the Copilot format. The two get different messages on purpose.
POLICY_REJECTED_AGENT_FIELDS: tuple[str, ...] = ("permissionMode", "hooks")
LEGACY_AGENT_FIELDS: tuple[str, ...] = ("argument-hint", "sub_agents")

VALID_SKILL_FRONTMATTER: str = """\
---
name: example-skill
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
argument-hint: "Describe what to do"
---

Body content.
"""

MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS: str = """\
---
name: bad-example
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
model: inherit
---

Body content.
"""
# Filename stem the fixtures above must be written under: Claude Code resolves
# an agent by its `name` field, so the lint requires the two to agree.
VALID_CLAUDE_AGENT_SLUG: str = "example-agent"
MISSING_TOOLS_AGENT_SLUG: str = "bad-example"

MALFORMED_SKILL_FRONTMATTER_SHORT_DESCRIPTION: str = """\
---
name: bad-skill
description: tooshort
argument-hint: "Pass an example argument"
---

Body content.
"""

# ── Postgres testcontainer parameters ─────────────────────────────────────────
POSTGRES_TEST_IMAGE: str = "postgres:16-alpine"
POSTGRES_TEST_DB: str = "mangomas_test"
POSTGRES_TEST_USER: str = "mangomas"
POSTGRES_TEST_PASSWORD: str = "mangomas_test"  # noqa: S105  test-only

# ── GCP Secret Manager E2E env-var names ──────────────────────────────────────
GCP_SECRETS_PROJECT_ENV: str = "GCP_SECRETS_PROJECT"
GCP_SECRETS_SECRET_NAME_ENV: str = "GCP_SECRETS_SECRET_NAME"  # noqa: S105  env-var name

# ── Eval harness (gate / sinks / scorers / discovery) ─────────────────────────
FAKE_SINK_NAME: str = "fake"
EVAL_SINK_CONSOLE: str = "console"
EVAL_SINK_JSON_FILE: str = "json_file"
EVAL_THRESHOLD_STRICT: float = 0.99
EVAL_THRESHOLD_LENIENT: float = 0.0
EVAL_SCHEMA_VERSION_CURRENT: int = 1
FAKE_PLUGIN_SCORER_NAME: str = "fake_plugin_scorer"
FAKE_PLUGIN_SINK_NAME: str = "fake_plugin_sink"
FAKE_PLUGIN_AGENT_NAME: str = "fake_plugin_agent"

# Bad option values used to prove scorer factories validate at construction
# time (before any row runs) rather than raising from inside score().
EVAL_BAD_REGEX_FLAG: str = "not-a-real-flag"
EVAL_BAD_REQUIRED_KEYS_OPTION: str = "not-a-list"
# A sqlite:/// URL whose naive (non-normalised) handling would create a
# literal "sqlite:" directory instead of resolving to the intended file.
EVAL_SQLITE_URL_PREFIX: str = "sqlite:///"

# ── Declarative workflow graphs (spec 0005) ───────────────────────────────────
WORKFLOW_NODE_KINDS: tuple[str, ...] = ("agent", "branch", "fan_out", "loop", "sequence")
WORKFLOW_SCHEMA_VERSION_CURRENT: int = 1
WORKFLOW_LOOP_SENTINEL: str = "DONE"
# The workflow CLI reuses the shared config/runtime exit codes rather than
# defining its own, so these alias the imported constants instead of restating
# 2 and 1 — a divergence would then be a failing test, not a stale comment.
WORKFLOW_CONFIG_EXIT_CODE: int = EXIT_CONFIG_ERROR
WORKFLOW_RUNTIME_EXIT_CODE: int = EXIT_RUNTIME_ERROR

# Workflow HTTP routes (spec 0008).
WORKFLOW_RUN_ROUTE: str = "/workflows/run"
WORKFLOW_VALIDATE_ROUTE: str = "/workflows/validate"

# ── Workflow E2E demo script (scripts/run_workflow_e2e.py) ────────────────────
WORKFLOW_E2E_SCRIPT: str = "run_workflow_e2e.py"
# Marker the script prints on its elapsed-time report line (happy path only).
WORKFLOW_E2E_ELAPSED_MARKER: str = "elapsed="
# Sentinel message for an injected generic dispatch failure.
WORKFLOW_E2E_FAILURE_MESSAGE: str = "workflow-e2e-dispatch-boom"
# Exit code the script returns on success (and on the MaxStepsExceeded path).
WORKFLOW_E2E_EXIT_OK: int = 0

# API authentication (spec 0010). The secret_ref is an env-var NAME (env provider);
# a non-MANGOMAS prefix keeps pydantic-settings from parsing it as a setting.
AUTH_SECRET_REF_ENV: str = "TEST_API_TOKEN"  # noqa: S105 — env-var name, not a secret
AUTH_TOKEN: str = "test-api-token-value"  # noqa: S105 — test fixture value, not a real secret

# Request backpressure (spec 0011).
BACKPRESSURE_MAX_BODY_BYTES: int = 10
BACKPRESSURE_MAX_CONCURRENT: int = 1

# Multi-tenancy (spec 0007 / ADR-0017).
TENANT_A: str = "tenant-a"
TENANT_B: str = "tenant-b"
TENANT_HEADER: str = "X-Tenant-ID"

# ── Shared prompt-resolution helpers / live temperature+max_tokens (spec 0014 M5) ──
# Distinct literal text so precedence-matrix assertions can tell explicit vs
# settings-sourced prompts apart unambiguously.
TEST_PROMPT_EXPLICIT: str = "Explicit constructor prompt."
TEST_PROMPT_SETTINGS: str = "Settings-provided prompt."
TEST_PROMPT_SUFFIX: str = "Suffix block."
# Distinct from DEFAULT_LLM_TEMPERATURE (0.2) so an override is observable;
# reused by both agent-level (AgentSettings) and adapter-level (LM Studio /
# Vertex request-payload) tests.
TEST_TEMPERATURE_OVERRIDE: float = 0.42
TEST_MAX_TOKENS_OVERRIDE: int = 256

# ── Claude Code ecosystem tooling (spec 0016 / ADR-0020) ─────────────────────
# Repo-relative paths to the two shared Claude Code config files.
CLAUDE_SETTINGS_RELPATH: str = ".claude/settings.json"
CLAUDE_SETTINGS_LOCAL_EXAMPLE_RELPATH: str = ".claude/settings.local.json.example"
MCP_CONFIG_RELPATH: str = ".mcp.json"

# ADR-0021 / spec-0017: the ConfigChange hook's opt-out env var, referenced
# by both the settings.local.json.example contract test and its
# documentation-pointer assertion.
HARNESS_CONFIG_AUDIT_MODE_ENV: str = "MANGOMAS_HARNESS__CONFIG_AUDIT_MODE"

# ── Live Claude Code corpus (spec-0018 / ADR-0024) ───────────────────────────
# Claude Code reads nothing from `.github/`; VS Code Copilot reads both roots,
# so `.claude/` is the single home that serves each tool.
CLAUDE_SKILLS_DIR_RELPATH: str = ".claude/skills"
RETIRED_SKILLS_DIR_RELPATH: str = ".github/skills"

# The roster, asserted by SET EQUALITY rather than by count: a count names
# nothing, whereas a set difference names the skill that appeared or vanished,
# and the one-line edit here is the review record for that change.
EXPECTED_SKILL_SLUGS: frozenset[str] = frozenset(
    {
        "mango-adapter",
        "mango-agent-add",
        "mango-config",
        "mango-coverage-audit",
        "mango-decompose",
        "mango-deploy",
        "mango-error",
        "mango-eval",
        "mango-harness",
        "mango-mutation-proof",
        "mango-observability",
        "mango-rag",
        "mango-release",
        "mango-testing",
        "mango-topology",
        "mango-workflow",
    }
)

# Docs that describe the corpus and must not point at a retired path. Historical
# records are excluded: CHANGELOG entries and dated plan documents describe the
# state at the time they were written and are deliberately immutable.
CORPUS_DOC_RELPATHS: tuple[str, ...] = (
    "CLAUDE.md",
    "README.md",
    "NEXT_STEPS.md",
    ".github/copilot-instructions.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/architecture/c2-container.md",
    "docs/tooling/claude-code-ecosystem.md",
    # Nested CLAUDE.md files. Claude Code auto-loads one when work happens
    # in its directory, so a stale pointer here is read before any work
    # starts — the same argument that puts the root CLAUDE.md on this list.
    "tests/CLAUDE.md",
    "src/mangomas/core/CLAUDE.md",
)

# Hooks that predate the ecosystem-tooling integration, as
# ``(event, matcher, command)``. Every `.claude/settings.json` edit must be
# additive, so the contract test asserts each of these survives verbatim —
# listing them here (rather than inline) keeps the expected hook contract in
# one place and lets the test stay data-driven.
# One command string, two registrations: the same stdin-JSON mode is wired
# under both the Edit-family matcher and Bash (it discriminates by payload
# shape). Named so the two tuples below cannot drift apart.
PRE_TOOL_USE_HOOK_COMMAND: str = "python scripts/lint_agent_frontmatter.py --hook pre-tool-use"

PREEXISTING_HOOKS: tuple[tuple[str, str, str], ...] = (
    ("SessionStart", "*", "python scripts/harness_session_start.py"),
    (
        # ADR-0021 / spec-0017: replaced the dead `$CLAUDE_TOOL_INPUT_path`
        # interpolation (Claude Code delivers hook input as stdin JSON, never
        # as a per-field env var) with the real `--hook pre-tool-use` stdin
        # mode, and widened the matcher to cover NotebookEdit.
        "PreToolUse",
        "Edit|Write|NotebookEdit",
        PRE_TOOL_USE_HOOK_COMMAND,
    ),
    (
        # Same stdin-JSON fix applied to the ruff-autofix hook, which had the
        # identical defect.
        "PostToolUse",
        "Edit|Write",
        # spec-0020: `ruff format` runs beside `check --fix` on the same emitted
        # path. Formatting is the one thing a per-file autofix hook genuinely
        # could not do before, so drift surfaced only at `make gate`. `-I{}`
        # replaces the bare `xargs` so both commands see the same argument.
        "python scripts/lint_agent_frontmatter.py --hook post-tool-use --emit-path "
        '| xargs -r -I{} sh -c \'python -m ruff check --fix "{}" >/dev/null 2>&1; '
        'python -m ruff format "{}" >/dev/null 2>&1\' || true',
    ),
    (
        # spec-0023 R8: `|| exit 1` replaced `|| true`. The recorded rationale
        # for swallowing ("so a failure never strands a session") did not hold:
        # only exit code 2 blocks a Stop hook, so any other non-zero was
        # already a *visible, non-blocking* notice. What `|| true` actually did
        # was downgrade that notice to a transcript-only line — and once the
        # zero-skip guard landed (spec-0022 R8, which turns a green run red by
        # mutating session.exitstatus) it was swallowing precisely the signal
        # the guard exists to raise. `exit 1` rather than bare propagation
        # because pytest exits 2 on a collection error, and 2 *would* block.
        # This is also the compensating control for the PostToolUse matcher
        # being Edit|Write only: nothing can know which files a Bash command
        # wrote, so `format-check` at turn end is the net that catches them.
        #
        # spec-0020: `make typecheck format-check` added ahead of the suite.
        # Measured at 0.3s warm, and mypy catches cross-file type breakage that
        # neither the per-file ruff hook nor pytest sees.
        #
        # `--cov` was considered and rejected: it costs +14s per turn end
        # (20.6s -> 35.0s) and would not have caught any of the three coverage
        # defects this repo has hit. Two were glob problems visible only in the
        # config, and the exclusion bug made the percentage go *up*. Delegates
        # to `make` for the same reason CI does — one definition of each check.
        "Stop",
        "*",
        "make typecheck format-check || exit 1 ; python -m pytest -q --no-cov || exit 1",
    ),
    (
        # spec-0022 R11: the same stdin-JSON pre-tool-use mode, registered a
        # second time under the Bash matcher. The mode discriminates by
        # payload shape (`tool_input.command` vs `file_path`), so one command
        # serves both matchers; for Bash it emits a mention-level advisory
        # `ask` on protected paths — never `deny`, per ADR-0021.
        "PreToolUse",
        "Bash",
        PRE_TOOL_USE_HOOK_COMMAND,
    ),
)

# rtk (rtk-ai/rtk): Bash-output compaction, wired as a PreToolUse hook.
RTK_HOOK_EVENT: str = "PreToolUse"
RTK_HOOK_MATCHER: str = "Bash"
RTK_HOOK_COMMAND_FRAGMENT: str = "rtk hook claude"
# Presence guard. Without it the hook exits 127 ("rtk: not found") on every
# Bash tool call for contributors who haven't installed the binary — Claude
# Code treats that as non-blocking, so the call still runs, but it emits a
# hook-error notice each time. The guard makes the skip silent.
RTK_BINARY_GUARD_FRAGMENT: str = "command -v rtk"
# Claude Code has no per-hook disable (``disableAllHooks`` would also drop the
# PREEXISTING_HOOKS above), so an env-var gate is the only per-contributor
# opt-out. ``env`` values layer across settings files; hook arrays do not.
RTK_DISABLE_ENV: str = "MANGOMAS_DISABLE_RTK_HOOK"
RTK_TELEMETRY_DISABLED_ENV: str = "RTK_TELEMETRY_DISABLED"
# Shared truthy/falsey markers for the `env`-block flags above.
ENV_FLAG_ON: str = "1"
ENV_FLAG_OFF: str = "0"

# ── Agent corpus contract (spec-0018 / ADR-0024) ──────────────────────────────
# Shared prefix on every tracked agent. It is what separates the committed
# corpus from a contributor's own agents in the same flat directory, and what
# keeps agent and skill names from colliding as both corpora grow.
AGENT_SLUG_PREFIX: str = "mango-"
CLAUDE_AGENTS_DIR_RELPATH: str = ".claude/agents"
RETIRED_AGENTS_DIR_RELPATH: str = ".github/agents"
# Five dormant `agent.md` files sat in the source tree, all containing claims
# that were false rather than stale. Two earned promotion to a nested
# CLAUDE.md; the other three were deleted as skill duplicates. Nothing may
# reintroduce the convention — a file nothing loads cannot be kept honest.
RETIRED_STRAY_AGENT_FILENAME: str = "agent.md"

# The 25 agents, by slug. Set equality, so a change names what appeared or
# vanished and editing this tuple is the review record.
EXPECTED_AGENT_SLUGS: tuple[str, ...] = (
    "mango-adr-author",
    "mango-agent-impl-dev",
    "mango-api-dev",
    "mango-api-impl-dev",
    "mango-architect",
    "mango-backend",
    "mango-ci-dev",
    "mango-cli-dev",
    "mango-error-taxonomy-dev",
    "mango-eval-dev",
    "mango-fake-builder",
    "mango-harness-dev",
    "mango-hypothesis-fuzz",
    "mango-integration-runner",
    "mango-layering-auditor",
    "mango-llm-adapter-dev",
    "mango-orchestrator-dev",
    "mango-pr-watcher",
    "mango-protocol-auditor",
    "mango-rag-dev",
    "mango-schema-evolution",
    "mango-secrets-dev",
    "mango-sse-streamer",
    "mango-storage-adapter-dev",
    "mango-telemetry-exporter-dev",
    "mango-test-engineer",
    "mango-workflow-graph-dev",
)
# Routers are the only agents that carry trigger conditions, so they are the
# only descriptions auto-delegation can match. They cannot write.
ROUTER_AGENT_SLUGS: frozenset[str] = frozenset(
    {"mango-architect", "mango-backend", "mango-api-dev", "mango-test-engineer"}
)
# Agents holding Edit and/or Write. A reviewed-change gate, NOT a substitute
# for a deny rule: it covers 18 of 25 and only fails when the set changes.
WRITE_CAPABLE_AGENT_SLUGS: frozenset[str] = frozenset(
    {
        "mango-adr-author",
        "mango-agent-impl-dev",
        "mango-api-impl-dev",
        "mango-ci-dev",
        "mango-cli-dev",
        "mango-error-taxonomy-dev",
        "mango-eval-dev",
        "mango-fake-builder",
        "mango-harness-dev",
        "mango-hypothesis-fuzz",
        "mango-integration-runner",
        "mango-llm-adapter-dev",
        "mango-orchestrator-dev",
        "mango-rag-dev",
        "mango-schema-evolution",
        "mango-secrets-dev",
        "mango-sse-streamer",
        "mango-storage-adapter-dev",
        "mango-telemetry-exporter-dev",
        "mango-workflow-graph-dev",
    }
)
# Agents whose declared surface includes a protected core contract. Each must
# say so, because the CI trailer gate will otherwise fail their first commit.
PROTECTED_PATH_OWNER_SLUGS: frozenset[str] = frozenset(
    {
        "mango-error-taxonomy-dev",
        "mango-orchestrator-dev",
        "mango-schema-evolution",
        "mango-hypothesis-fuzz",
    }
)
# The description is the entire routing surface and loads at every session
# start. 320 binds on the corpus as written; 400 would bind on nothing.
AGENT_DESCRIPTION_MAX_CHARS: int = 320
# Ceiling on token overlap between two *router* descriptions. Routers are the
# only auto-delegated agents, so they are the only pair that can compete. A
# ratchet, stated honestly: the observed maximum is 0.208, so this binds on
# nothing today and exists to stop drift.
MAX_ROUTER_DESCRIPTION_JACCARD: float = 0.30
# Trigger-condition wording. Banned outside routers: "invoke explicitly when X"
# is not a control, because auto-delegation matches X and ignores the verb.
AGENT_TRIGGER_PHRASE_PATTERN: str = r"use when|when you|whenever"
# Wording retired with the parent/child hierarchy.
RETIRED_AGENT_PREFIX: str = "Sub-agent of"
# Minimum ADR/spec references across the corpus. Set from what the corpus
# actually contains (ADR-0013/0014/0016 and `spec 0012`) rather than an
# aspiration — note the space in `spec 0012`, which a `spec-\\d{4}` regex misses.
MIN_CORPUS_TRACEABILITY_REFS: int = 4

# ── R7: skills own procedure, agents own a surface (spec-0018) ────────────────
# Agents whose surface an existing skill already documents. Such an agent must
# name its skill and must not restate the recipe: before this mapping,
# mango-llm-adapter-dev duplicated ~45 of its 64 body lines from mango-adapter,
# and mango-telemetry-exporter-dev had copied ~26 lines of mango-deploy while
# citing no skill at all.
#
# Authored, not derived — a reviewer should check the pairings rather than
# trust them. Routers and auditors are deliberately absent: their numbered
# steps are their own operating loop, not a recipe a skill owns.
AGENT_SKILL_OWNERS: dict[str, tuple[str, ...]] = {
    "mango-adr-author": ("mango-release",),
    "mango-agent-impl-dev": ("mango-agent-add",),
    "mango-api-impl-dev": ("mango-observability", "mango-config"),
    "mango-ci-dev": ("mango-deploy", "mango-mutation-proof"),
    "mango-error-taxonomy-dev": ("mango-error",),
    "mango-eval-dev": ("mango-eval",),
    "mango-fake-builder": ("mango-testing",),
    "mango-harness-dev": ("mango-harness",),
    "mango-hypothesis-fuzz": ("mango-testing",),
    "mango-integration-runner": ("mango-testing",),
    "mango-llm-adapter-dev": ("mango-adapter",),
    "mango-orchestrator-dev": ("mango-topology", "mango-observability"),
    "mango-pr-watcher": ("mango-release",),
    "mango-rag-dev": ("mango-rag",),
    "mango-schema-evolution": ("mango-agent-add",),
    # Two skills, deliberately: `mango-adapter` supplies the Protocol-first
    # contract and the fake pattern, but its "register the factory" rule and
    # its async-methods rule are both wrong for this surface (the secrets
    # registry stores instances, and the protocol is sync-only). `mango-config`
    # is what actually documents the SecretsProvider seam.
    "mango-secrets-dev": ("mango-adapter", "mango-config"),
    "mango-sse-streamer": ("mango-topology",),
    "mango-storage-adapter-dev": ("mango-adapter",),
    "mango-telemetry-exporter-dev": ("mango-observability", "mango-deploy"),
    "mango-workflow-graph-dev": ("mango-workflow", "mango-observability"),
}
# Agents deliberately outside `AGENT_SKILL_OWNERS`, so the mapping can be
# checked for totality: a new agent must land in one set or the other, never
# fall through both unnoticed. Two reasons appear here, and both are decisions
# rather than omissions:
#
#   * the four routers and the two auditors have no file surface at all — they
#     read and advise, and their numbered steps are their own operating loop,
#     not a recipe any skill owns (which is also why
#     `test_mapped_agent_has_no_procedure_section` is scoped to mapped agents);
#   * `mango-cli-dev` owns a real surface that no skill documents, so its
#     workflow is genuinely its own rather than a restated recipe.
#
# Adding a slug here is therefore a claim that no skill documents its
# procedure. `test_every_agent_is_mapped_or_recorded_unmapped` enforces the
# partition; `test_mapped_agent_references_its_skill` enforces the other half.
# Top-level entries under `src/mangomas/` that no write-capable agent claims in
# its `## Surface You Own` section. `registry.py` is deliberate rather than an
# omission: it is a protected path holding a generic `Registry[T]` with no
# project-specific logic, consumed equally by the agent, eval, workflow,
# secrets and node registries. Handing it to any one of those owners would be
# arbitrary, and a change to it is a cross-cutting contract change that needs a
# `BREAKING-CHANGE` trailer and an architecture review, not a surface owner.
#
# Everything else must be claimed. `test_every_source_surface_has_a_write_capable_owner`
# derives ownership from the agent bodies themselves rather than a second
# hand-maintained table, so the corpus cannot desync from its own claims.
UNOWNED_SOURCE_SURFACES: frozenset[str] = frozenset({"registry.py"})

# ── Corpus-count claims in prose ─────────────────────────────────────────────
#
# Docs that describe the corpus as it is *now*. A number in one of these is a
# claim about the live tree and must agree with it; the README said "13 skills,
# 23 agents (4 routers + 19 specialists)" while the tree held 15 and 27.
#
# `NEXT_STEPS.md` is deliberately absent. It is a dated delivery log whose
# per-milestone counts are correct *as of that milestone* and must not be
# rewritten — its own preamble says "counts below are as-of the harness
# branch". Rewriting them would falsify the record rather than fix drift.
# `docs/adr/` and `docs/plans/` are excluded for the same reason.
LIVE_CORPUS_COUNT_DOCS: tuple[str, ...] = (
    "CLAUDE.md",
    "README.md",
    "docs/tooling/claude-code-ecosystem.md",
)
# Numbers written as words, which the corpus docs use in prose.
#
# "one" is deliberately absent. In English it doubles as an article — "dispatch
# one agent", "one skill owns the procedure" — so treating it as a count claim
# produces false positives on ordinary prose, and no doc will ever truthfully
# claim this corpus holds a single agent. Every other word is unambiguous
# because it forces a plural noun.
SPELLED_NUMBERS: dict[str, int] = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
}
# A count claim whose noun is a corpus noun but whose subject is a *subset*.
# Keyed by a distinctive phrase on the line; the value names the roster the
# number must equal. Registered rather than exempted — a subset count is still
# a claim, and this keeps it pinned to something real.
SUBSET_COUNT_CLAIMS: dict[str, str] = {
    "own a **protected path**": "PROTECTED_PATH_OWNER_SLUGS",
}
SKILL_UNMAPPED_AGENT_SLUGS: frozenset[str] = frozenset(
    {
        "mango-api-dev",
        "mango-architect",
        "mango-backend",
        "mango-cli-dev",
        "mango-layering-auditor",
        "mango-protocol-auditor",
        "mango-test-engineer",
    }
)
# The four protected-path owners additionally reference the governance skill.
HARNESS_SKILL_SLUG: str = "mango-harness"
# Heading a mapped agent may not carry: its procedure belongs to its skill.
# Unmapped agents keep theirs.
PROCEDURE_SECTION_HEADING: str = "## Workflow"
# Canonical `##` vocabulary for agent bodies. 56 distinct headings existed
# before this, including three spellings of "surface you own", which made the
# corpus unscannable and let duplicated sections hide under new names.
# Nine, not seven: agents whose surface is a *process* rather than a file tree
# (the auditors, mango-pr-watcher) need `## Checklist` and `## Decision Table`
# to say what they actually do. Each of the nine has a distinct meaning, which
# is the property that matters — an ad-hoc name is where a duplicated section
# hides.
AGENT_SECTION_HEADINGS: frozenset[str] = frozenset(
    {
        "## Surface You Own",
        "## Protected path",
        "## Invariants",
        "## Constraints",
        "## Checklist",
        "## Decision Table",
        "## Diagnosing Failures",
        "## Output Format",
        "## Workflow",
    }
)
# `### Breaking Changes` was prescribed in four places and used in CHANGELOG.md
# zero times — it is not a Keep a Changelog section, which is the format the
# CHANGELOG declares. The enforced mechanism is the commit trailer.
RETIRED_CHANGELOG_HEADING: str = "### Breaking Changes"

# Claude Code permission rules in `.claude/settings.json`. Pinned by set
# equality: nothing else in the suite asserted anything about `permissions`, so
# a rule could be dropped, or added unreviewed, in total silence.
EXPECTED_DENY_RULES: frozenset[str] = frozenset(
    {
        "Bash(rm -rf:*)",
        "Bash(git push --force:*)",
        "Bash(git push -f:*)",
        # The two settings files, not `.claude/**`. The rationale for denying
        # anything here is that nothing legitimately needs Claude Code to
        # rewrite its own permissions mid-session — and that argues for these
        # two files, not the whole tree. A `.claude/**` rule would also block
        # every edit to `.claude/skills/`, which is ordinary authoring work and
        # is exactly what the next planned change does.
        "Edit(/.claude/settings.json)",
        # Gitignored, still loaded, and able to add `permissions.allow` entries
        # — so it is the more useful of the two to deny.
        "Edit(/.claude/settings.local.json)",
        # spec-0022 R4: MCP filesystem-write and git-mutation tools bypass both
        # the `Edit|Write|NotebookEdit` PreToolUse matcher and the `Edit(...)`
        # deny rules — the MCP half of the gap ADR-0021 explicitly concedes.
        # The permission layer is the only in-session-authoritative one, so the
        # deny lands here. File edits and git mutations keep their first-class,
        # harness-audited channels (native Edit/Write and Bash git).
        "mcp__filesystem__write_file",
        "mcp__filesystem__edit_file",
        "mcp__filesystem__move_file",
        "mcp__filesystem__create_directory",
        "mcp__git__git_commit",
        "mcp__git__git_add",
        "mcp__git__git_reset",
        "mcp__git__git_checkout",
        "mcp__git__git_create_branch",
        "mcp__git__git_init",
    }
)
# Every MCP-tool deny rule is `mcp__<server>__<tool>`; the server segment must
# name an adopted server or the rule is silently inert (a dead control that
# reads like a live one — the same defect class as the interior-`*` Bash rule).
MCP_DENY_RULE_PREFIX: str = "mcp__"
# Deny rules Claude Code consults for a *file write*. `Edit(...)` covers Edit,
# Write and NotebookEdit; nothing here stops a `Bash` heredoc or `>` redirect,
# so these rules are cheap and partial rather than airtight.
PATH_SCOPED_DENY_RULE_PREFIX: str = "Edit("
# A leading `/` anchors a rule at the settings file's directory (the project
# root). Without it the rule is cwd-relative and silently stops matching when
# a session starts from a subdirectory.
ANCHORED_RULE_PATH_PREFIX: str = "/"
# Claude Code honours exactly one wildcard in a Bash rule: a trailing `:*` after
# the command prefix. An interior `*` is matched as a literal character, so
# `Bash(python -m ruff *:*)` never matched `python -m ruff check --fix` and the
# call prompted on every run — a dead allow-rule that looks live.
BASH_RULE_PREFIX: str = "Bash("
BASH_RULE_WILDCARD_SUFFIX: str = ":*"
# Rule heads Claude Code accepts but never consults for a file write, emitting a
# startup warning instead. Only `Edit(...)` is matched, and it already covers
# Edit, Write and NotebookEdit — so a `Write(...)` rule is a non-control that
# reads like one.
INERT_FILE_RULE_PREFIXES: tuple[str, ...] = ("Write(", "NotebookEdit(")

# MCP servers adopted by ADR-0020. Upstream `memory` (redundant with
# claude-mem), `everything` (test/demo), and `time` (low value here) are
# deliberately excluded — the test asserts an exact set so an unreviewed
# addition fails.
ADOPTED_MCP_SERVERS: frozenset[str] = frozenset(
    {"filesystem", "git", "fetch", "sequential-thinking", "repomix", "github"}
)
# The only adopted server needing a credential, and the only one that is
# optional: without docker or a token it fails to start and the other five are
# unaffected. The npm `@modelcontextprotocol/server-github` package was NOT
# used — upstream deprecated it ("Package no longer supported"), so the npx
# form every other server here uses is not available for this one.
CREDENTIALED_MCP_SERVERS: frozenset[str] = frozenset({"github"})
# Any secret an MCP server needs must arrive as a `${VAR}` interpolation. A
# literal value in this shared, checked-in file would be a committed
# credential, and cloud sessions load it with no approval prompt.
ENV_INTERPOLATION_PREFIX: str = "${"
# Servers that take a path argument and must be pinned to the project root
# rather than granted unscoped filesystem/git reach.
PATH_SCOPED_MCP_SERVERS: tuple[str, ...] = ("filesystem", "git")
# `${VAR:-default}` form: without the default an unset variable is passed
# through as a literal string rather than failing, which is the dangerous case.
MCP_PROJECT_DIR_SCOPE: str = "${CLAUDE_PROJECT_DIR:-.}"

# ── Env-gated suite skip reasons (spec-0022 R8) ───────────────────────────────
# Single source for three consumers: the collection gate in tests/conftest.py
# builds its skip marks from these, the zero-skip session guard treats exactly
# these reasons as sanctioned, and tests/tooling/test_collection_gate.py
# asserts the wiring in a subprocess.
#
# Stored as {env_var: what-it-runs} and *formatted* into the reason, rather
# than storing the full sentence: the reason repeats its own key, so a
# hand-written table admits `{"RUN_RAG": "set RUN_LANGFUSE=1 to run RAG..."}`
# — a typo the gate would emit, the guard would sanction (it is in .values()),
# and no test would catch. Deriving it makes that desync unrepresentable.
ENV_GATE_SUITES: dict[str, str] = {
    "RUN_INTEGRATION": "integration tests",
    "RUN_LMSTUDIO": "LM Studio tests",
    "RUN_POSTGRES": "Postgres tests",
    "RUN_VERTEX": "Vertex AI tests",
    "RUN_GCP_SECRETS": "GCP Secret Manager tests",
    "RUN_GCP_TRACE": "Cloud Trace exporter tests",
    "RUN_EMBEDDINGS_LOCAL": "sentence-transformers tests",
    "RUN_RAG": "chromadb-backed RAG tests",
    "RUN_LANGFUSE": "Langfuse sink tests",
    "RUN_GITLEAKS": "gitleaks config behaviour tests",
}


def env_gate_skip_reason(env_var: str, suite: str) -> str:
    """Render the one sanctioned skip-reason sentence shape."""
    return f"set {env_var}=1 to run {suite}"


ENV_GATE_SKIP_REASONS: dict[str, str] = {
    env: env_gate_skip_reason(env, suite) for env, suite in ENV_GATE_SUITES.items()
}
# Runtime `pytest.skip(...)` calls inside already-enabled gated suites (the
# vertex/gcp fixtures that additionally need a project id) phrase their reason
# `set <VAR> to run <suite>`. Applied with `fullmatch`, not `match`: a prefix
# test would sanction `pytest.skip("set VERTEX_X ... actually just flaky")`.
# The alternation is derived from the env-var names the suites actually use,
# so it cannot rot away from them.
GATED_RUNTIME_SKIP_REASON_PREFIXES: tuple[str, ...] = ("VERTEX_", "GCP_")
GATED_RUNTIME_SKIP_REASON_RE: str = (
    r"^set (?:" + "|".join(GATED_RUNTIME_SKIP_REASON_PREFIXES) + r")\w+ to run [\w \-]+$"
)

# ── CLI public surface (tests/test_cli_surface.py) ────────────────────────────
# `mangomas` is a console script (`pyproject.toml` -> `mangomas.cli.main:app`),
# so its command tree and flags are a user-facing contract. Recorded from the
# live app and then reviewed — editing these tuples is the review record for a
# surface change, exactly like EXPECTED_AGENT_SLUGS is for the corpus.
EXPECTED_CLI_ROOT_COMMANDS: tuple[str, ...] = (
    "agents",
    "chat",
    "eval",
    "history",
    "rag",
    "workflow",
)
EXPECTED_CLI_COMMANDS: tuple[str, ...] = (
    "agents",
    "chat",
    "eval",
    "history",
    "rag",
    "rag ingest",
    "rag query",
    "workflow",
    "workflow run",
    "workflow validate",
)
# Long-form options plus positional arguments, per command. Short aliases (-a,
# -v) are deliberately excluded: they are conveniences, and pinning them would
# make the set churn without protecting anything a script depends on.
EXPECTED_CLI_PARAMS: dict[str, tuple[str, ...]] = {
    "agents": (),
    "chat": ("--agent", "--system", "--verbose", "<message>"),
    "eval": (
        "--agent",
        "--allow-new-failures",
        "--baseline",
        "--dataset",
        "--dataset-source",
        "--fail-fast",
        "--fail-on-error",
        "--gate",
        "--max-mean-score-drop",
        "--max-pass-rate-drop",
        "--min-mean-score",
        "--min-pass-rate",
        "--no-allow-new-failures",
        "--no-fail-fast",
        "--no-fail-on-error",
        "--no-gate",
        "--output-json",
        "--parallelism",
        "--scorer",
        "--target",
        "--verbose",
    ),
    "history": ("--limit", "--verbose"),
    "rag": (),
    "rag ingest": ("--verbose", "<path>"),
    "rag query": ("--top-k", "--verbose", "<text>"),
    "workflow": (),
    "workflow run": ("--definition", "--verbose", "<message>"),
    "workflow validate": ("--definition", "--verbose"),
}

# `--help` listing order, per group. Registration order, NOT alphabetical:
# the root is agents/chat/history/eval/rag/workflow and `workflow` is
# validate/run. Typer emits registered_commands before registered_groups, so
# sub-apps always follow root commands; the order within each bucket is a
# deliberate choice and a user-visible surface.
EXPECTED_CLI_HELP_ORDER: dict[str, tuple[str, ...]] = {
    "<root>": ("agents", "chat", "history", "eval", "rag", "workflow"),
    "rag": ("ingest", "query"),
    "workflow": ("validate", "run"),
}

# ── Shipped canonical workflow example (roadmap item 1.3) ─────────────────────
# Repo-root-relative path of the shipped planner → tool → reviewer graph; tests
# resolve it against their own location so the suite stays cwd-independent.
PLAN_EXECUTE_REVIEW_GRAPH_RELPATH = "examples/workflows/plan-execute-review.json"
PLAN_EXECUTE_REVIEW_GRAPH_NAME = "plan-execute-review"
# Ordered roster of the pipeline's agent slugs (mirrors the example file).
PLAN_EXECUTE_REVIEW_AGENTS: tuple[str, ...] = ("planner", "tool", "reviewer")

# ── Per-step timeout test values (spec-0026) ──────────────────────────────────
# A step budget far below the slow agent's sleep, so the timeout test fires
# fast and deterministically; the sleep itself is cancelled by the expiring
# `asyncio.timeout`, so its nominal length is never actually waited out.
TINY_STEP_TIMEOUT_SECONDS: float = 0.02
SLOW_AGENT_DELAY_SECONDS: float = 5.0
# A short real delay used to pin the no-timeout regression: with
# `loop_settings=None` a step slower than TINY_STEP_TIMEOUT_SECONDS must still
# complete (there is no clock to cancel it).
UNTIMED_AGENT_DELAY_SECONDS: float = 0.05
