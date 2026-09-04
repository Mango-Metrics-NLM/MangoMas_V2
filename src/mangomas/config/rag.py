"""Retrieval settings: embeddings, vector store, and chunking.

`MANGOMAS_EMBEDDINGS__*`, `MANGOMAS_VECTOR__*`, `MANGOMAS_RAG__*`. All three
are opt-in and default-off; retrieval needs the first two together."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from mangomas.config._shared import DEFAULT_VERTEX_LOCATION

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


# Torch device for the in-process ``sentence_transformers`` backend. ``None``
# (the default) passes no ``device`` argument at all, leaving the library's own
# auto-detect (CUDA → MPS → CPU) exactly as it was before this setting existed.
#
# Set it when auto-detect picks the wrong thing: a box whose GPU is already
# serving an LLM wants ``cpu`` for embeddings, and a CI runner wants to prove a
# forced-CPU run matches an auto-detected one. Ignored by the ``lmstudio`` and
# ``vertex`` backends, which run the model out of process.
DEFAULT_EMBEDDINGS_DEVICE: str | None = None


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
    # sentence_transformers-specific: the torch device. Unset ⇒ the library's
    # own auto-detect, so existing deployments are unaffected (spec-0029 R5).
    device: str | None = DEFAULT_EMBEDDINGS_DEVICE
    # Vertex-specific (required only when provider="vertex"; ADC auth).
    project_id: str | None = None
    location: str = DEFAULT_VERTEX_LOCATION


# Vector store defaults — consumed when MANGOMAS_VECTOR__ENABLED=true.
DEFAULT_VECTOR_ENABLED: bool = False


DEFAULT_VECTOR_PROVIDER: str = "chroma"


DEFAULT_VECTOR_PERSIST_DIR: str = "./data/chroma"


DEFAULT_VECTOR_COLLECTION: str = "mangomas"


DEFAULT_VECTOR_TOP_K: int = 5


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


# RAG ingestion/chunking defaults.
DEFAULT_RAG_CHUNK_WORDS: int = 800


DEFAULT_RAG_CHUNK_OVERLAP: int = 120


DEFAULT_RAG_MIN_CHUNK_WORDS: int = 50


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
