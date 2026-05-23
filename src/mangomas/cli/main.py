"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import typer

from mangomas.composition import build_orchestrator
from mangomas.core import AgentRequest, Message

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator

app = typer.Typer(help="Mango-Mas V2 CLI", no_args_is_help=True)

logger = logging.getLogger(__name__)


def _build() -> Orchestrator:
    """Construct the orchestrator with full settings + adapter wiring."""
    return build_orchestrator()


async def _close_orchestrator(orch: Orchestrator) -> None:
    """Release adapter resources cleanly.

    Dispatches on ``hasattr(repo, "aclose")`` to support async-pool
    backends (PostgresRepository) without breaking the sync ``close()``
    contract used by SQLite. Mirrors the FastAPI lifespan close path so
    CLI invocations don't leak asyncpg connections on exit.
    """
    ctx = orch.context
    if hasattr(ctx.llm, "aclose"):
        await ctx.llm.aclose()
    if ctx.repo is not None:
        if hasattr(ctx.repo, "aclose"):
            await ctx.repo.aclose()
        else:
            ctx.repo.close()
    if ctx.memory is not None:
        ctx.memory.close()


@app.command()
def agents() -> None:
    """List registered agents."""
    orch = _build()
    try:
        for name in orch.list_agents():
            typer.echo(name)
    finally:
        asyncio.run(_close_orchestrator(orch))


@app.command()
def chat(
    message: str = typer.Argument(..., help="User message"),
    agent: str = typer.Option("chat", "--agent", "-a", help="Agent name"),
    system: str | None = typer.Option(None, "--system", "-s", help="Optional system prompt"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Send a one-shot message to an agent and print the reply."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    orch = _build()
    messages: list[Message] = []
    if system:
        messages.append(Message(role="system", content=system))
    messages.append(Message(role="user", content=message))

    request = AgentRequest(messages=messages)
    try:
        response = asyncio.run(orch.dispatch(agent, request))
        typer.echo(response.content)
    finally:
        asyncio.run(_close_orchestrator(orch))


@app.command()
def history(
    limit: int = typer.Option(10, "--limit", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Print recent persisted turns as JSON lines."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    orch = _build()
    try:
        repo = orch.context.repo
        if repo is None:
            typer.echo("No repository configured.", err=True)
            raise typer.Exit(code=1)
        rows = asyncio.run(repo.list_turns(limit=limit))
        for row in rows:
            typer.echo(json.dumps(row, ensure_ascii=False))
    finally:
        asyncio.run(_close_orchestrator(orch))


if __name__ == "__main__":  # pragma: no cover
    app()
