"""LLM provider factories.

Factories for instantiating LLM clients from configuration. Each factory
accepts LLMSettings and returns a fully-initialized LLMClient implementation.

Lazy imports (e.g., Vertex SDK) are deferred so optional extras stay optional.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.adapters.llm import LMStudioClient
from mangomas.config import LLMSettings

logger = logging.getLogger(__name__)


def _lmstudio_factory(cfg: LLMSettings) -> LMStudioClient:
    """Build an LMStudioClient from LLMSettings.

    Returns an HTTP-based LLM client configured to connect to a local LM Studio server.
    """
    logger.debug(
        "Building LMStudioClient",
        extra={"base_url": cfg.base_url, "model": cfg.model},
    )
    return LMStudioClient(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
        default_temperature=cfg.temperature,
    )


def _vertex_factory(cfg: LLMSettings) -> Any:
    """Build a VertexClient from LLMSettings.

    Returns a Google Cloud Vertex AI client. When ``secret_ref`` is set,
    ``api_key`` is treated as a service-account JSON body and forwarded as
    ``credentials_json``. Otherwise falls back to ``credentials_path`` (or
    Application Default Credentials when both are absent).

    VertexClient is imported from the composition module (rather than directly
    from adapters.llm) to allow tests to monkeypatch it. The SDK is lazily
    loaded inside VertexClient's constructor, keeping the optional ``vertex``
    extra genuinely optional.
    """
    import mangomas.composition as composition_module  # noqa: PLC0415

    logger.debug(
        "Building VertexClient",
        extra={"project_id": cfg.project_id, "location": cfg.location, "model": cfg.model},
    )
    credentials_json = cfg.api_key if cfg.secret_ref else None
    return composition_module.VertexClient(
        project_id=cfg.project_id,
        location=cfg.location,
        model=cfg.model,
        credentials_path=cfg.credentials_path,
        credentials_json=credentials_json,
        timeout_seconds=cfg.timeout_seconds,
        default_temperature=cfg.temperature,
    )


__all__ = [
    "_lmstudio_factory",
    "_vertex_factory",
]
