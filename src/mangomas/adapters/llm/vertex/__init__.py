"""Vertex AI adapter."""

from __future__ import annotations

from ._client import VertexClient, VertexError
from ._client import _translate_vertex_error as _translate_vertex_error

__all__ = ["VertexClient", "VertexError"]
