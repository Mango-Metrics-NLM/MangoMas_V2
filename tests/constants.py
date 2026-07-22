"""Shared test constants.

Import these instead of repeating magic literals in tests.
Values mirror the defaults defined in ``mangomas.config``.
"""

from __future__ import annotations

# ── LLM defaults ──────────────────────────────────────────────────────────────
DEFAULT_LLM_PROVIDER: str = "lmstudio"
DEFAULT_LLM_BASE_URL: str = "http://localhost:1234/v1"
DEFAULT_MODEL: str = "local-model"
DEFAULT_API_KEY: str = "lm-studio"
DEFAULT_TIMEOUT_SECONDS: float = 60.0
DEFAULT_TEMPERATURE: float = 0.2

# ── LM Studio E2E env-var names (single source of truth) ──────────────────────
LMSTUDIO_BASE_URL_ENV: str = "LMSTUDIO_BASE_URL"
LMSTUDIO_MODEL_ENV: str = "LMSTUDIO_MODEL"

# ── Vertex AI E2E env-var names (single source of truth) ──────────────────────
VERTEX_PROJECT_ENV: str = "VERTEX_PROJECT_ID"
VERTEX_LOCATION_ENV: str = "VERTEX_LOCATION"
VERTEX_MODEL_ENV: str = "VERTEX_MODEL"
VERTEX_CREDENTIALS_PATH_ENV: str = "VERTEX_CREDENTIALS_PATH"
RUN_VERTEX_ENV: str = "RUN_VERTEX"
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

# ── Embeddings defaults / mock ────────────────────────────────────────────────
DEFAULT_EMBEDDINGS_PROVIDER: str = "lmstudio"
DEFAULT_EMBEDDINGS_MODEL: str = "local-model"
DEFAULT_EMBEDDINGS_BATCH_SIZE: int = 32
TEST_EMBEDDINGS_MOCK_MODEL: str = "embed-m"

# ── Vector store / RAG defaults ───────────────────────────────────────────────
DEFAULT_VECTOR_PROVIDER: str = "chroma"
DEFAULT_VECTOR_PERSIST_DIR: str = "./data/chroma"
DEFAULT_VECTOR_COLLECTION: str = "mangomas"
DEFAULT_VECTOR_TOP_K: int = 5
DEFAULT_RAG_CHUNK_WORDS: int = 800
DEFAULT_RAG_CHUNK_OVERLAP: int = 120
DEFAULT_RAG_MIN_CHUNK_WORDS: int = 50
TEST_VECTOR_PERSIST_DIR: str = "./data/test-chroma"
TEST_VECTOR_COLLECTION: str = "test-col"

# ── DB defaults ───────────────────────────────────────────────────────────────
DEFAULT_DB_PROVIDER: str = "sqlite"
DEFAULT_DB_URL: str = "sqlite:///./data/mangomas.db"

# ── API defaults ──────────────────────────────────────────────────────────────
DEFAULT_API_HOST: str = "0.0.0.0"  # noqa: S104
DEFAULT_API_PORT: int = 8000

# ── Agent / reply stubs ───────────────────────────────────────────────────────
DEFAULT_AGENT_NAME: str = "chat"
STUB_REPLY: str = "stub-reply"

# ── Control loop ──────────────────────────────────────────────────────────────
DEFAULT_LOOP_MAX_STEPS: int = 1

# ── Tool stubs ────────────────────────────────────────────────────────────────
DEFAULT_TOOL_NAME: str = "echo"
DEFAULT_TOOL_RESULT: str = "echo-result"

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
RUN_LANGFUSE_ENV: str = "RUN_LANGFUSE"
FAKE_SINK_NAME: str = "fake"
EVAL_SINK_CONSOLE: str = "console"
EVAL_SINK_JSON_FILE: str = "json_file"
EVAL_SINK_LANGFUSE: str = "langfuse"
# Exit code the CLI raises when the quality gate fails (mirrors
# mangomas.cli.main.EVAL_GATE_EXIT_CODE).
EVAL_GATE_EXIT_CODE: int = 3
EVAL_THRESHOLD_STRICT: float = 0.99
EVAL_THRESHOLD_LENIENT: float = 0.0
EVAL_SCHEMA_VERSION_CURRENT: int = 1
FAKE_PLUGIN_SCORER_NAME: str = "fake_plugin_scorer"
FAKE_PLUGIN_SINK_NAME: str = "fake_plugin_sink"
FAKE_PLUGIN_AGENT_NAME: str = "fake_plugin_agent"

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
