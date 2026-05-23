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
