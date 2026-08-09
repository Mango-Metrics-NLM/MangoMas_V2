"""Shared test constants.

Import these instead of repeating magic literals in tests.
Defaults that mirror ``mangomas.config`` are re-exported from it below so a
config change can never silently desync the tests.
"""

from __future__ import annotations

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
    DEFAULT_RAG_CHUNK_OVERLAP as DEFAULT_RAG_CHUNK_OVERLAP,
)
from mangomas.config import (
    DEFAULT_RAG_CHUNK_WORDS as DEFAULT_RAG_CHUNK_WORDS,
)
from mangomas.config import (
    DEFAULT_RAG_MIN_CHUNK_WORDS as DEFAULT_RAG_MIN_CHUNK_WORDS,
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
name: BadExample
description: A sufficiently descriptive blurb that satisfies the linter minimum length.
model: Claude Sonnet 4.5 (copilot)
argument-hint: "Pass an example argument"
---

Body content.
"""

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
# Exit code the CLI raises when the quality gate fails (mirrors
# mangomas.cli.main.EVAL_GATE_EXIT_CODE).
EVAL_GATE_EXIT_CODE: int = 3
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
# Exit code the CLI raises for a workflow *config* error (mirrors eval's exit 2).
WORKFLOW_CONFIG_EXIT_CODE: int = 2
WORKFLOW_RUNTIME_EXIT_CODE: int = 1

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

# Hooks that predate the ecosystem-tooling integration, as
# ``(event, matcher, command)``. Every `.claude/settings.json` edit must be
# additive, so the contract test asserts each of these survives verbatim —
# listing them here (rather than inline) keeps the expected hook contract in
# one place and lets the test stay data-driven.
PREEXISTING_HOOKS: tuple[tuple[str, str, str], ...] = (
    ("SessionStart", "*", "python scripts/harness_session_start.py"),
    (
        # ADR-0021 / spec-0017: replaced the dead `$CLAUDE_TOOL_INPUT_path`
        # interpolation (Claude Code delivers hook input as stdin JSON, never
        # as a per-field env var) with the real `--hook pre-tool-use` stdin
        # mode, and widened the matcher to cover NotebookEdit.
        "PreToolUse",
        "Edit|Write|NotebookEdit",
        "python scripts/lint_agent_frontmatter.py --hook pre-tool-use",
    ),
    (
        # Same stdin-JSON fix applied to the ruff-autofix hook, which had the
        # identical defect.
        "PostToolUse",
        "Edit|Write",
        "python scripts/lint_agent_frontmatter.py --hook post-tool-use --emit-path "
        "| xargs -r python -m ruff check --fix 2>/dev/null || true",
    ),
    ("Stop", "*", "python -m pytest -q --no-cov || true"),
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

# MCP servers adopted by ADR-0020. Upstream `memory` (redundant with
# claude-mem), `everything` (test/demo), and `time` (low value here) are
# deliberately excluded — the test asserts an exact set so an unreviewed
# addition fails.
ADOPTED_MCP_SERVERS: frozenset[str] = frozenset(
    {"filesystem", "git", "fetch", "sequential-thinking", "repomix"}
)
# Servers that take a path argument and must be pinned to the project root
# rather than granted unscoped filesystem/git reach.
PATH_SCOPED_MCP_SERVERS: tuple[str, ...] = ("filesystem", "git")
# `${VAR:-default}` form: without the default an unset variable is passed
# through as a literal string rather than failing, which is the dangerous case.
MCP_PROJECT_DIR_SCOPE: str = "${CLAUDE_PROJECT_DIR:-.}"
