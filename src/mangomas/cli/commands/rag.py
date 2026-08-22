"""The `mangomas rag` sub-app: `ingest` and `query`.

Unlike the root commands, these are decorated here. `rag_app` is local to this
module, so the decorators are ordinary statements in one file — nothing an
import reorder can permute — and `mangomas.cli._app` only has to `add_typer`
it. That keeps the `ingest, query` listing order pinned by statement order.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import typer

from mangomas.cli import _runtime
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR
from mangomas.config import get_settings
from mangomas.rag import IngestionPipeline, IngestReport, Retriever

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.embeddings.base import EmbeddingClient
    from mangomas.adapters.vector.base import VectorStoreRepository
    from mangomas.core import Orchestrator

rag_app = typer.Typer(help="Retrieval-augmented generation commands", no_args_is_help=True)


def _require_rag(orch: Orchestrator) -> tuple[EmbeddingClient, VectorStoreRepository]:
    """Return ``(embeddings, vector_store)`` or exit(2) if RAG is not enabled."""
    ctx = orch.context
    if ctx.embeddings is None or ctx.vector_store is None:
        typer.echo(
            "RAG is not enabled. Set MANGOMAS_EMBEDDINGS__ENABLED=true and "
            "MANGOMAS_VECTOR__ENABLED=true.",
            err=True,
        )
        raise typer.Exit(code=EXIT_CONFIG_ERROR)
    return ctx.embeddings, ctx.vector_store


@rag_app.command(name="ingest")
def rag_ingest(
    path: str = typer.Argument(..., help="File or directory of *.txt / *.md to ingest"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Chunk, embed and upsert documents into the vector store."""
    _runtime.configure_cli_logging(verbose=verbose)

    cfg = get_settings()
    orch = _runtime._build()

    async def _run() -> IngestReport:
        try:
            embeddings, vector_store = _require_rag(orch)
            pipeline = IngestionPipeline(
                embeddings=embeddings,
                vector_store=vector_store,
                settings=cfg.rag,
                batch_size=cfg.embeddings.batch_size,
            )
            return await pipeline.ingest(path)
        finally:
            await _runtime._close_orchestrator(orch)

    report = asyncio.run(_run())
    typer.echo(
        f"ingested docs={report.documents} chunks={report.chunks} "
        f"batches={report.batches} deleted_sources={report.deleted_sources}"
    )


@rag_app.command(name="query")
def rag_query(
    text: str = typer.Argument(..., help="The query text to retrieve context for"),
    top_k: int | None = typer.Option(None, "--top-k", "-k", help="Max passages to return"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Embed a query, search the vector store, and print ranked context."""
    _runtime.configure_cli_logging(verbose=verbose)

    cfg = get_settings()
    orch = _runtime._build()

    async def _run() -> list[str]:
        try:
            embeddings, vector_store = _require_rag(orch)
            retriever = Retriever(
                embeddings=embeddings,
                vector_store=vector_store,
                top_k=cfg.vector.top_k,
            )
            results = await retriever.search(text, top_k=top_k)
            return [
                f"[{i + 1}] score={r.score:.3f} source={r.chunk.source}\n{r.chunk.text}"
                for i, r in enumerate(results)
            ]
        finally:
            await _runtime._close_orchestrator(orch)

    lines = asyncio.run(_run())
    if not lines:
        typer.echo("No relevant context found.")
        return
    for line in lines:
        typer.echo(line)
