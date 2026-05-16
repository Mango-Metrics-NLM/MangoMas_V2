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
