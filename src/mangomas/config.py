"""Application configuration via pydantic-settings.

All settings can be overridden by env vars with the ``MANGOMAS_`` prefix and
``__`` as the nested delimiter (e.g. ``MANGOMAS_LLM__BASE_URL``).

Default values are also exposed as module-level ``DEFAULT_*`` constants so
tests can import them instead of repeating magic literals.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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

DEFAULT_EMBEDDINGS_ENABLED: bool = False
DEFAULT_EMBEDDINGS_PROVIDER: str = "lmstudio"
# Neutral placeholder mirroring DEFAULT_LLM_MODEL — set explicitly per provider:
# e.g. ``all-MiniLM-L6-v2`` (sentence-transformers), ``text-embedding-004``
# (Vertex), or the loaded LM Studio embedding model id.
DEFAULT_EMBEDDINGS_MODEL: str = "local-model"
DEFAULT_EMBEDDINGS_BASE_URL: str = "http://localhost:1234/v1"
DEFAULT_EMBEDDINGS_API_KEY: str = "lm-studio"
DEFAULT_EMBEDDINGS_BATCH_SIZE: int = 32
DEFAULT_EMBEDDINGS_TIMEOUT_SECONDS: float = 60.0

# Vector store defaults — consumed when MANGOMAS_VECTOR__ENABLED=true.
DEFAULT_VECTOR_ENABLED: bool = False
DEFAULT_VECTOR_PROVIDER: str = "chroma"
DEFAULT_VECTOR_PERSIST_DIR: str = "./data/chroma"
DEFAULT_VECTOR_COLLECTION: str = "mangomas"
DEFAULT_VECTOR_TOP_K: int = 5

# RAG ingestion/chunking defaults.
DEFAULT_RAG_CHUNK_WORDS: int = 800
DEFAULT_RAG_CHUNK_OVERLAP: int = 120
DEFAULT_RAG_MIN_CHUNK_WORDS: int = 50

DEFAULT_SECRETS_PROVIDER: str = "env"
# GCP Secret Manager defaults — consumed when MANGOMAS_SECRETS__PROVIDER=gcp.
DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS: float = 5.0
DEFAULT_GCP_SECRET_VERSION: str = "latest"  # noqa: S105 — not a secret value

# Maximum length of the ``detail`` field on structured error envelopes /
# log records. Bounds untrusted exception text so that adapter exception
# bodies (which can include URLs, payload fragments, or remote stack
# traces) can never blow out a log line or an HTTP response body.
DEFAULT_ERROR_DETAIL_TRUNCATE: int = 200

# Evaluation harness defaults.
DEFAULT_EVAL_SCORER: str = "exact_match"
DEFAULT_EVAL_AGENT: str = "chat"
# Default eval target. ``agent`` dispatches a single registered agent (the
# pre-target-indirection behaviour); other built-ins are ``pipeline`` /
# ``fan_out`` / ``echo``. Resolved through ``target_registry``.
DEFAULT_EVAL_TARGET: str = "agent"
# Default dataset source. ``jsonl`` reads a local file (the historical loader);
# other built-ins are ``inline`` / ``langfuse``. Resolved through
# ``dataset_source_registry``.
DEFAULT_EVAL_DATASET_SOURCE: str = "jsonl"
DEFAULT_EVAL_OUTPUT_DIR: str = "eval-output"
DEFAULT_EVAL_PARALLELISM: int = 1
DEFAULT_EVAL_FAIL_FAST: bool = False

# Quality-gate defaults — all OFF so existing runs keep exit code 0 on success.
# ``min_mean_score`` / ``min_pass_rate`` stay ``None`` (no threshold). When a
# threshold is set the CLI exits 3 if the report falls below it.
DEFAULT_EVAL_GATE_ENABLED: bool = False
DEFAULT_EVAL_MIN_MEAN_SCORE: float | None = None
DEFAULT_EVAL_MIN_PASS_RATE: float | None = None
DEFAULT_EVAL_FAIL_ON_ERROR: bool = False

# Result sinks — ``console`` reproduces today's inline stdout summary exactly,
# so the default is behaviour-preserving. Held as a tuple (immutable module
# constant); the field builds a fresh list from it via ``default_factory``.
DEFAULT_EVAL_SINKS: tuple[str, ...] = ("console",)

# Default per-request timeout (seconds) for the optional ``webhook`` sink's
# httpx POST. Overridable per-sink via ``sink_options["webhook"]["timeout_seconds"]``.
DEFAULT_EVAL_WEBHOOK_TIMEOUT_SECONDS: float = 10.0

# Forward-compatible config version marker. Bump when EvalSettings grows a
# field that needs migration; a config declaring a *higher* version than the
# code supports logs a warning rather than crashing.
DEFAULT_EVAL_SCHEMA_VERSION: int = 1

DEFAULT_HARNESS_ENABLED: bool = False
DEFAULT_HARNESS_METRICS_NAMESPACE: str = "mangomas.harness"
DEFAULT_HARNESS_HOOK_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING"] = "INFO"


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


class EmbeddingSettings(BaseModel):
    """Embedding-provider configuration.

    Gated by ``enabled`` (default ``False``) exactly like
    :class:`MemorySettings`, so default behaviour is unchanged. ``provider``
    selects the backend: ``lmstudio`` | ``sentence_transformers`` | ``vertex``.
    The LM Studio fields (``base_url``/``api_key``) and the Vertex fields
    (``project_id``/``location``) are only consulted by their respective
    factories.
    """

    enabled: bool = DEFAULT_EMBEDDINGS_ENABLED
    provider: str = DEFAULT_EMBEDDINGS_PROVIDER
    model: str = DEFAULT_EMBEDDINGS_MODEL
    base_url: str = DEFAULT_EMBEDDINGS_BASE_URL
    api_key: str = DEFAULT_EMBEDDINGS_API_KEY
    batch_size: int = DEFAULT_EMBEDDINGS_BATCH_SIZE
    timeout_seconds: float = DEFAULT_EMBEDDINGS_TIMEOUT_SECONDS
    # Vertex-specific (required only when provider="vertex"; ADC auth).
    project_id: str | None = None
    location: str = DEFAULT_VERTEX_LOCATION


class VectorSettings(BaseModel):
    """Vector store configuration.

    Gated by ``enabled`` (default ``False``) like :class:`MemorySettings`, so
    default behaviour is unchanged. ``provider`` selects the backend (``chroma``);
    ``persist_dir`` / ``collection`` configure on-disk storage and ``top_k`` is
    the default retrieval depth.
    """

    enabled: bool = DEFAULT_VECTOR_ENABLED
    provider: str = DEFAULT_VECTOR_PROVIDER
    persist_dir: str = DEFAULT_VECTOR_PERSIST_DIR
    collection: str = DEFAULT_VECTOR_COLLECTION
    top_k: int = DEFAULT_VECTOR_TOP_K


class RagSettings(BaseModel):
    """RAG ingestion + chunking parameters (word-window chunker).

    Invariants are validated at construction so a bad ``MANGOMAS_RAG__*`` value
    (e.g. an overlap that meets or exceeds the window) fails fast at settings
    load rather than surfacing deep inside the ingestion pipeline.
    """

    chunk_words: int = DEFAULT_RAG_CHUNK_WORDS
    chunk_overlap: int = DEFAULT_RAG_CHUNK_OVERLAP
    min_chunk_words: int = DEFAULT_RAG_MIN_CHUNK_WORDS

    @model_validator(mode="after")
    def _check_window(self) -> RagSettings:
        if self.chunk_words < 1:
            raise ValueError(f"chunk_words must be >= 1 (got {self.chunk_words})")
        if self.min_chunk_words < 0:
            raise ValueError(f"min_chunk_words must be >= 0 (got {self.min_chunk_words})")
        if self.chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be >= 0 (got {self.chunk_overlap})")
        if self.chunk_overlap >= self.chunk_words:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be < chunk_words ({self.chunk_words})"
            )
        return self


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


class HarnessSettings(BaseModel):
    """Claude Code harness telemetry and hook configuration.

    All fields default to safe no-op values so existing callers behave
    identically when this group is absent from the environment.
    """

    enabled: bool = DEFAULT_HARNESS_ENABLED
    metrics_namespace: str = DEFAULT_HARNESS_METRICS_NAMESPACE
    hook_log_level: Literal["DEBUG", "INFO", "WARNING"] = DEFAULT_HARNESS_HOOK_LOG_LEVEL


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

    # ── Target indirection ────────────────────────────────────────────────────
    # ``target`` selects what each row dispatches against, resolved through
    # ``target_registry`` (default ``agent`` == today's single-agent dispatch).
    # ``target_options`` carries per-target kwargs keyed by target name, e.g.
    # ``{"pipeline": {"agents": ["planner", "reviewer"]}}``.
    target: str = DEFAULT_EVAL_TARGET
    target_options: dict[str, dict[str, object]] = Field(default_factory=dict)

    # ── Dataset source ────────────────────────────────────────────────────────
    # ``dataset_source`` selects where rows come from, resolved through
    # ``dataset_source_registry`` (default ``jsonl`` == today's file loader).
    # ``dataset_source_options`` carries per-source kwargs keyed by source name,
    # e.g. ``{"inline": {"rows": [...]}}``. For ``jsonl`` the ``--dataset`` flag /
    # ``dataset_path`` still supplies the path.
    dataset_source: str = DEFAULT_EVAL_DATASET_SOURCE
    dataset_source_options: dict[str, dict[str, object]] = Field(default_factory=dict)

    # ── Quality gate (CI) — default OFF ───────────────────────────────────────
    gate_enabled: bool = DEFAULT_EVAL_GATE_ENABLED
    min_mean_score: float | None = DEFAULT_EVAL_MIN_MEAN_SCORE
    min_pass_rate: float | None = DEFAULT_EVAL_MIN_PASS_RATE
    fail_on_error: bool = DEFAULT_EVAL_FAIL_ON_ERROR

    # ── Result sinks ──────────────────────────────────────────────────────────
    # Ordered list of sink names resolved through ``sink_registry``. Defaults to
    # ``["console"]`` (== today's inline output). ``sink_options`` carries
    # per-sink kwargs keyed by sink name, e.g. ``{"json_file": {"path": "..."}}``.
    sinks: list[str] = Field(default_factory=lambda: list(DEFAULT_EVAL_SINKS))
    sink_options: dict[str, dict[str, object]] = Field(default_factory=dict)

    # ── Forward-compatible schema version ─────────────────────────────────────
    schema_version: int = DEFAULT_EVAL_SCHEMA_VERSION

    @model_validator(mode="after")
    def _validate_eval(self) -> EvalSettings:
        """Bound gate thresholds to ``[0, 1]`` and tolerate future schema versions.

        Thresholds are normalised scores, so a value outside ``[0, 1]`` is a
        configuration error (fail fast at construction). A ``schema_version``
        ahead of what this build supports is *not* fatal — it is logged so a
        newer config can be read by older code without crashing (forward-compat).
        """
        for label, value in (
            ("min_mean_score", self.min_mean_score),
            ("min_pass_rate", self.min_pass_rate),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"eval.{label} must be in [0.0, 1.0]; got {value}")
        if self.schema_version > DEFAULT_EVAL_SCHEMA_VERSION:
            logger.warning(
                "Eval config declares a future schema_version; reading with current code",
                extra={
                    "event": "eval_config_future_version",
                    "declared": self.schema_version,
                    "supported": DEFAULT_EVAL_SCHEMA_VERSION,
                },
            )
        return self


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
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    vector: VectorSettings = Field(default_factory=VectorSettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    harness: HarnessSettings = Field(default_factory=HarnessSettings)
    eval: EvalSettings = Field(default_factory=EvalSettings)

    # Set to True to enable entry-point-based agent discovery (Phase C).
    discovery_enabled: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
