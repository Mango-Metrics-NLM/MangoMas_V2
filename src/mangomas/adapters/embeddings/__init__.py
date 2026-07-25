"""Embedding adapters."""

from __future__ import annotations

from mangomas.adapters.embeddings.base import EmbeddingClient
from mangomas.adapters.embeddings.lmstudio import LMStudioEmbeddingClient
from mangomas.adapters.embeddings.sentence_transformers import (
    SentenceTransformersEmbeddingClient,
)
from mangomas.adapters.embeddings.vertex import VertexEmbeddingClient

__all__ = [
    "EmbeddingClient",
    "LMStudioEmbeddingClient",
    "SentenceTransformersEmbeddingClient",
    "VertexEmbeddingClient",
]
