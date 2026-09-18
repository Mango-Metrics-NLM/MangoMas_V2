"""Root commands that talk to an orchestrator: `agents`, `chat`, `history`.

Plain functions, not decorated commands — `mangomas.cli._app` registers them.
A `@app.command()` here would need `app`, and importing the assembly root from
a command module is the import cycle `mangomas.cli.__init__` already sets up.
"""

from __future__ import annotations

import asyncio
import json
import logging

import typer

from mangomas.cli import _runtime
from mangomas.cli.exit_codes import EXIT_RUNTIME_ERROR
from mangomas.config import DEFAULT_API_HISTORY_DEFAULT_LIMIT
from mangomas.core import AgentRequest, Message

logger = logging.getLogger(__name__)


def agents() -> None:
    """List registered agents."""
    orch = _runtime._build()

    async def _run() -> list[str]:
        try:
            return list(orch.list_agents())
        finally:
            await _runtime._close_orchestrator(orch)

    for name in asyncio.run(_run()):
        typer.echo(name)


def chat(
    message: str = typer.Argument(..., help="User message"),
    agent: str = typer.Option("chat", "--agent", "-a", help="Agent name"),
    system: str | None = typer.Option(None, "--system", "-s", help="Optional system prompt"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Send a one-shot message to an agent and print the reply."""
    _runtime.configure_cli_logging(verbose=verbose)

    orch = _runtime._build()
    messages: list[Message] = []
    if system:
        messages.append(Message(role="system", content=system))
    messages.append(Message(role="user", content=message))

    request = AgentRequest(messages=messages)

    async def _run() -> str:
        try:
            response = await orch.dispatch(agent, request)
            return response.content
        finally:
            await _runtime._close_orchestrator(orch)

    typer.echo(asyncio.run(_run()))


def history(
    limit: int = typer.Option(DEFAULT_API_HISTORY_DEFAULT_LIMIT, "--limit", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Print recent persisted turns as JSON lines."""
    _runtime.configure_cli_logging(verbose=verbose)

    orch = _runtime._build()

    async def _run() -> list[dict[str, object]] | None:
        """Return rows, or ``None`` to signal "no repository configured"."""
        try:
            repo = orch.context.repo
            if repo is None:
                return None
            return await repo.list_turns(limit=limit)
        finally:
            await _runtime._close_orchestrator(orch)

    rows = asyncio.run(_run())
    if rows is None:
        typer.echo("No repository configured.", err=True)
        raise typer.Exit(code=EXIT_RUNTIME_ERROR)

    for row in rows:
        typer.echo(json.dumps(row, ensure_ascii=False))
