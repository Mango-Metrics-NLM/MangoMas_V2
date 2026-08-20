"""LLM provider settings (`MANGOMAS_LLM__*`)."""

from __future__ import annotations

from pydantic import BaseModel

from mangomas.config._shared import DEFAULT_VERTEX_LOCATION

# ── Module-level defaults (single source of truth) ────────────────────────────

DEFAULT_LLM_PROVIDER: str = "lmstudio"


DEFAULT_LLM_BASE_URL: str = "http://localhost:1234/v1"


DEFAULT_LLM_MODEL: str = "local-model"


DEFAULT_LLM_API_KEY: str = "lm-studio"


DEFAULT_LLM_TIMEOUT_SECONDS: float = 60.0


DEFAULT_LLM_TEMPERATURE: float = 0.2


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
