"""Retrieval-augmented generation: chunking, ingestion, and retrieval."""

from __future__ import annotations

from mangomas.rag.chunker import chunk_text
from mangomas.rag.loader import ParseFailure, RawDoc, iter_documents, load_documents
from mangomas.rag.models import Chunk, SearchResult
from mangomas.rag.pipeline import IngestionPipeline, IngestReport
from mangomas.rag.retrieval import RetrievalTool, Retriever

__all__ = [
    "Chunk",
    "IngestReport",
    "IngestionPipeline",
    "ParseFailure",
    "RawDoc",
    "RetrievalTool",
    "Retriever",
    "SearchResult",
    "chunk_text",
    "iter_documents",
    "load_documents",
]
