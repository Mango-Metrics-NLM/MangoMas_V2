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

# ── Vector store test-scoped values ───────────────────────────────────────────
TEST_VECTOR_PERSIST_DIR: str = "./data/test-chroma"
TEST_VECTOR_COLLECTION: str = "test-col"

# ── Agent / reply stubs ───────────────────────────────────────────────────────
DEFAULT_AGENT_NAME: str = "chat"
STUB_REPLY: str = "stub-reply"

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
