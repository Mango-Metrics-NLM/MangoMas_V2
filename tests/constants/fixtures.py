"""Test-scoped stubs, eval/workflow fixtures, and prompt helpers."""

from __future__ import annotations

from mangomas.cognitive.constants import (
    JSONL_FILENAME,
    UNPARSED_GOAL,
    UNPARSED_PLANNER_STEP,
)
from tests.constants.cli import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR

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

__all__ = [
    "ASGI_TEST_BASE_URL",
    "AUTH_SECRET_REF_ENV",
    "AUTH_TOKEN",
    "BACKPRESSURE_MAX_BODY_BYTES",
    "BACKPRESSURE_MAX_CONCURRENT",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_TOOL_NAME",
    "DEFAULT_TOOL_RESULT",
    "DEFAULT_VERTEX_EMBEDDING_MODEL",
    "EVAL_BAD_REGEX_FLAG",
    "EVAL_BAD_REQUIRED_KEYS_OPTION",
    "EVAL_SCHEMA_VERSION_CURRENT",
    "EVAL_SINK_CONSOLE",
    "EVAL_SINK_JSON_FILE",
    "EVAL_SQLITE_URL_PREFIX",
    "EVAL_THRESHOLD_LENIENT",
    "EVAL_THRESHOLD_STRICT",
    "FAKE_PLUGIN_AGENT_NAME",
    "FAKE_PLUGIN_SCORER_NAME",
    "FAKE_PLUGIN_SINK_NAME",
    "FAKE_SINK_NAME",
    "GCP_SECRETS_PROJECT_ENV",
    "GCP_SECRETS_SECRET_NAME_ENV",
    "JSONL_FILENAME",
    "LARGE_UPSTREAM_BODY_CHARS",
    "LMSTUDIO_EMBEDDING_MODEL_ENV",
    "PLAN_EXECUTE_REVIEW_AGENTS",
    "PLAN_EXECUTE_REVIEW_GRAPH_NAME",
    "PLAN_EXECUTE_REVIEW_GRAPH_RELPATH",
    "POSTGRES_TEST_DB",
    "POSTGRES_TEST_IMAGE",
    "POSTGRES_TEST_PASSWORD",
    "POSTGRES_TEST_USER",
    "SLOW_AGENT_DELAY_SECONDS",
    "STUB_REPLY",
    "SUMMARIZE_HISTORY_LIMIT_ENV",
    "TENANT_A",
    "TENANT_B",
    "TENANT_HEADER",
    "TEST_EMBEDDINGS_MOCK_MODEL",
    "TEST_HISTORY_LIMIT",
    "TEST_HISTORY_LIMIT_OVERRIDE",
    "TEST_HISTORY_SEEDED_TURNS",
    "TEST_LMSTUDIO_MOCK_BASE_URL",
    "TEST_LMSTUDIO_MOCK_MODEL",
    "TEST_MAX_TOKENS_OVERRIDE",
    "TEST_PROMPT_EXPLICIT",
    "TEST_PROMPT_SETTINGS",
    "TEST_PROMPT_SUFFIX",
    "TEST_TEMPERATURE_OVERRIDE",
    "TEST_TOOL_MAX_STEPS",
    "TEST_TOOL_MAX_STEPS_OVERRIDE",
    "TEST_TOOL_SYSTEM_PROMPT",
    "TEST_VECTOR_COLLECTION",
    "TEST_VECTOR_PERSIST_DIR",
    "TEST_VERTEX_PROJECT",
    "TINY_STEP_TIMEOUT_SECONDS",
    "TOOL_MAX_STEPS_ENV",
    "UNPARSED_GOAL",
    "UNPARSED_PLANNER_STEP",
    "UNTIMED_AGENT_DELAY_SECONDS",
    "VERTEX_EMBEDDING_MODEL_ENV",
    "WORKFLOW_CONFIG_EXIT_CODE",
    "WORKFLOW_E2E_ELAPSED_MARKER",
    "WORKFLOW_E2E_EXIT_OK",
    "WORKFLOW_E2E_FAILURE_MESSAGE",
    "WORKFLOW_E2E_SCRIPT",
    "WORKFLOW_LOOP_SENTINEL",
    "WORKFLOW_NODE_KINDS",
    "WORKFLOW_RUNTIME_EXIT_CODE",
    "WORKFLOW_RUN_ROUTE",
    "WORKFLOW_SCHEMA_VERSION_CURRENT",
    "WORKFLOW_VALIDATE_ROUTE",
]
