"""Embedding provider factories.

Factories for instantiating embedding clients from configuration.
Lazy imports (e.g., sentence-transformers, Vertex SDK) are deferred so
optional extras stay optional.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.adapters.embeddings import LMStudioEmbeddingClient
from mangomas.config import EmbeddingSettings

logger = logging.getLogger(__name__)


def _lmstudio_embedding_factory(cfg: EmbeddingSettings) -> LMStudioEmbeddingClient:
    """Build an LMStudioEmbeddingClient from EmbeddingSettings.

    Returns an HTTP-based embedding client configured to connect to a local
    LM Studio server.
    """
    logger.debug(
        "Building LMStudioEmbeddingClient",
        extra={"base_url": cfg.base_url, "model": cfg.model},
    )
    return LMStudioEmbeddingClient(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
    )


def _sentence_transformers_embedding_factory(cfg: EmbeddingSettings) -> Any:
    """Build a SentenceTransformersEmbeddingClient (lazy heavy import inside).

    Returns an in-process embedding client using Hugging Face sentence-transformers.
    The SDK is lazy-imported to keep the optional ``embeddings-local`` extra
    genuinely optional.
    """
    logger.debug(
        "Building SentenceTransformersEmbeddingClient",
        # `device` is logged because it is the setting most likely to explain
        # a surprise: a machine that silently fell back to CPU, or one whose
        # GPU is being contended by an LLM, looks like "embeddings got slow"
        # with nothing in the logs to say why.
        extra={"model": cfg.model, "device": cfg.device},
    )
    from mangomas.adapters.embeddings.sentence_transformers import (  # noqa: PLC0415
        SentenceTransformersEmbeddingClient,
    )

    return SentenceTransformersEmbeddingClient(model=cfg.model, device=cfg.device)


def _vertex_embedding_factory(cfg: EmbeddingSettings) -> Any:
    """Build a VertexEmbeddingClient (ADC auth; SDK deferred inside the client).

    Returns a Google Cloud Vertex AI embedding client. Uses Application Default
    Credentials (ADC) for authentication. The Google Cloud SDK is lazy-imported
    to keep the optional ``vertex`` extra genuinely optional.
    """
    logger.debug(
        "Building VertexEmbeddingClient",
        extra={"project_id": cfg.project_id, "location": cfg.location, "model": cfg.model},
    )
    from mangomas.adapters.embeddings.vertex import VertexEmbeddingClient  # noqa: PLC0415

    return VertexEmbeddingClient(
        project_id=cfg.project_id,
        location=cfg.location,
        model=cfg.model,
    )


__all__ = [
    "_lmstudio_embedding_factory",
    "_sentence_transformers_embedding_factory",
    "_vertex_embedding_factory",
]
