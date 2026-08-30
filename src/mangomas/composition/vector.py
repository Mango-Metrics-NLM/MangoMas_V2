"""Vector store provider factory.

Factories for instantiating vector store backends from configuration.
Lazy imports (e.g., chromadb SDK) are deferred so optional extras stay optional.
"""

from __future__ import annotations

import logging
from typing import Any

from mangomas.config import VectorSettings

logger = logging.getLogger(__name__)


def _chroma_vector_factory(cfg: VectorSettings) -> Any:
    """Build a ChromaVectorStore (lazy chromadb import inside the client).

    Returns a Chroma-backed vector store for semantic search and retrieval.
    The chromadb SDK is lazy-imported to keep the optional ``rag`` extra
    genuinely optional.
    """
    logger.debug(
        "Building ChromaVectorStore",
        extra={"persist_dir": cfg.persist_dir, "collection": cfg.collection},
    )
    from mangomas.adapters.vector.chroma import ChromaVectorStore  # noqa: PLC0415

    return ChromaVectorStore(persist_dir=cfg.persist_dir, collection_name=cfg.collection)


__all__ = [
    "_chroma_vector_factory",
]
