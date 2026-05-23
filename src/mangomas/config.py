"""Application configuration via pydantic-settings.

All settings can be overridden by env vars with the ``MANGOMAS_`` prefix and
``__`` as the nested delimiter (e.g. ``MANGOMAS_LLM__BASE_URL``).

Default values are also exposed as module-level ``DEFAULT_*`` constants so
tests can import them instead of repeating magic literals.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Module-level defaults (single source of truth) ────────────────────────────

DEFAULT_LLM_PROVIDER: str = "lmstudio"
DEFAULT_LLM_BASE_URL: str = "http://localhost:1234/v1"
DEFAULT_LLM_MODEL: str = "local-model"
DEFAULT_LLM_API_KEY: str = "lm-studio"
DEFAULT_LLM_TIMEOUT_SECONDS: float = 60.0
DEFAULT_LLM_TEMPERATURE: float = 0.2

# Vertex AI provider defaults. ``project_id``/``credentials_path`` have no
# safe defaults — they must be supplied explicitly via env vars when using
# the ``vertex`` provider.
DEFAULT_VERTEX_LOCATION: str = "us-central1"

DEFAULT_DB_PROVIDER: str = "sqlite"
DEFAULT_DB_URL: str = "sqlite:///./data/mangomas.db"
# asyncpg pool + connection knobs — consumed when MANGOMAS_DB__PROVIDER=postgres.
DEFAULT_DB_POOL_MIN: int = 1
DEFAULT_DB_POOL_MAX: int = 10
DEFAULT_DB_CONNECT_TIMEOUT_SECONDS: float = 10.0
DEFAULT_DB_STATEMENT_TIMEOUT_SECONDS: float | None = None

DEFAULT_API_HOST: str = "0.0.0.0"  # noqa: S104
DEFAULT_API_PORT: int = 8000
DEFAULT_API_READY_TIMEOUT: float = 2.0

DEFAULT_LOG_FORMAT: Literal["text", "json"] = "text"
DEFAULT_LOG_BODY_TRUNCATE: int = 512

DEFAULT_LOOP_MAX_STEPS: int = 1
DEFAULT_LOOP_STEP_TIMEOUT: float = 30.0
DEFAULT_TOOL_MAX_STEPS: int = 5

DEFAULT_MEMORY_PROVIDER: str = "file"
DEFAULT_MEMORY_DIR: str = "memory"
DEFAULT_MEMORY_INDEX: str = "MEMORY.md"
DEFAULT_MEMORY_ENABLED: bool = False

DEFAULT_SECRETS_PROVIDER: str = "env"
# GCP Secret Manager defaults — consumed when MANGOMAS_SECRETS__PROVIDER=gcp.
DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS: float = 5.0
DEFAULT_GCP_SECRET_VERSION: str = "latest"  # noqa: S105  not a secret value

# Maximum length of the ``detail`` field on structured error envelopes /
# log records. Bounds untrusted exception text so that adapter exception
# bodies (which can include URLs, payload fragments, or remote stack
# traces) can never blow out a log line or an HTTP response body.
DEFAULT_ERROR_DETAIL_TRUNCATE: int = 200

# Evaluation harness defaults.
DEFAULT_EVAL_SCORER: str = "exact_match"
DEFAULT_EVAL_AGENT: str = "chat"
DEFAULT_EVAL_OUTPUT_DIR: str = "eval-output"
DEFAULT_EVAL_PARALLELISM: int = 1
DEFAULT_EVAL_FAIL_FAST: bool = False


# ── Sub-settings models ────────────────────────────────────────────────────────


class LLMSettings(BaseModel):
    """LLM endpoint configuration (LM Studio by default; OpenAI-compatible).

    Vertex-specific fields (``project_id``, ``location``, ``credentials_path``)
    are optional and only consulted when ``provider == "vertex"``. They default
    to ``None``/``DEFAULT_VERTEX_LOCATION`` so existing LM Studio deployments
    see no behaviour change.
    """

    provider: str = DEFAULT_LLM_PROVIDER
    base_url: str = DEFAULT_LLM_BASE_URL
    model: str = DEFAULT_LLM_MODEL
    api_key: str = DEFAULT_LLM_API_KEY
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    temperature: float = DEFAULT_LLM_TEMPERATURE
    # Optional reference resolved via the SecretsProvider seam. When set,
    # the resolved value overrides ``api_key`` at orchestrator-build time.
    # For Vertex this resolved value is treated as a service-account JSON body.
    # See ``mangomas.secrets`` and ``composition._lmstudio_factory``.
    secret_ref: str | None = None
    # Vertex-specific fields (required only when provider="vertex").
    project_id: str | None = None
    location: str = DEFAULT_VERTEX_LOCATION
    credentials_path: str | None = None


class SecretsSettings(BaseModel):
    """Configuration for the SecretsProvider seam."""

    provider: str = DEFAULT_SECRETS_PROVIDER
    # GCP Secret Manager fields (required only when provider="gcp"; validated
    # at factory-build time in composition.py).
    project_id: str | None = None
    timeout_seconds: float = DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS
    default_version: str = DEFAULT_GCP_SECRET_VERSION


class DBSettings(BaseModel):
    """Persistence configuration."""

    provider: str = DEFAULT_DB_PROVIDER
    url: str = DEFAULT_DB_URL
    # asyncpg pool + connection knobs — used only by the postgres provider.
    pool_min: int = DEFAULT_DB_POOL_MIN
    pool_max: int = DEFAULT_DB_POOL_MAX
    connect_timeout_seconds: float = DEFAULT_DB_CONNECT_TIMEOUT_SECONDS
    statement_timeout_seconds: float | None = DEFAULT_DB_STATEMENT_TIMEOUT_SECONDS


class APISettings(BaseModel):
    """HTTP server configuration."""

    host: str = DEFAULT_API_HOST
    port: int = DEFAULT_API_PORT
    ready_timeout_seconds: float = DEFAULT_API_READY_TIMEOUT


class LogSettings(BaseModel):
    """Logging format and filtering configuration."""

    format: Literal["text", "json"] = DEFAULT_LOG_FORMAT
    body_truncate: int = DEFAULT_LOG_BODY_TRUNCATE


class AgentSettings(BaseModel):
    """Per-agent overrides loaded from ``MANGOMAS_AGENTS__<NAME>__*`` env vars."""

    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    model_override: str | None = None


class LoopSettings(BaseModel):
    """Iterative control-loop parameters."""

    max_steps: int = DEFAULT_LOOP_MAX_STEPS
    step_timeout_seconds: float = DEFAULT_LOOP_STEP_TIMEOUT


class MemorySettings(BaseModel):
    """Dual-layer markdown memory configuration."""

    enabled: bool = DEFAULT_MEMORY_ENABLED
    provider: str = DEFAULT_MEMORY_PROVIDER
    memory_dir: str = DEFAULT_MEMORY_DIR
    index_file: str = DEFAULT_MEMORY_INDEX


class EvalSettings(BaseModel):
    """Evaluation harness configuration.

    ``dataset_path`` has no safe default — the CLI requires it explicitly so
    that ``mangomas eval`` never runs against an unintended dataset. The
    rest of the fields ship safe defaults so most invocations can rely on
    ``MANGOMAS_EVAL__DATASET_PATH=...`` alone.
    """

    dataset_path: str | None = None
    scorer: str = DEFAULT_EVAL_SCORER
    agent: str = DEFAULT_EVAL_AGENT
    output_dir: str = DEFAULT_EVAL_OUTPUT_DIR
    parallelism: int = DEFAULT_EVAL_PARALLELISM
    fail_fast: bool = DEFAULT_EVAL_FAIL_FAST
    # Free-form per-scorer options (e.g. ``{"threshold": 0.8}``). Forwarded
    # verbatim to the scorer's factory.
    scorer_options: dict[str, object] = Field(default_factory=dict)


class Settings(BaseSettings):
    """Top-level application settings."""

    model_config = SettingsConfigDict(
        env_prefix="MANGOMAS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "dev", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    llm: LLMSettings = Field(default_factory=LLMSettings)
    db: DBSettings = Field(default_factory=DBSettings)
    api: APISettings = Field(default_factory=APISettings)
    log: LogSettings = Field(default_factory=LogSettings)

    # Per-agent overrides keyed by agent name.
    agents: dict[str, AgentSettings] = Field(default_factory=dict)

    loop: LoopSettings = Field(default_factory=LoopSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    eval: EvalSettings = Field(default_factory=EvalSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
