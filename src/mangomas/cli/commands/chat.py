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
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from mangomas.composition import LLM_PROFILE_METADATA_KEY
from mangomas.config import DEFAULT_API_HISTORY_DEFAULT_LIMIT, DEFAULT_API_HISTORY_MAX_LIMIT
from mangomas.core import AgentRequest, Message

logger = logging.getLogger(__name__)


def agents() -> None:
    """List registered agents."""
    # No ``--verbose`` flag (the option surface is pinned by
    # tests/test_cli_surface.py), but the bootstrap still runs so this command
    # honours MANGOMAS_LOG_LEVEL / MANGOMAS_LOG__FORMAT like its siblings.
    _runtime.configure_cli_logging()

    orch = _runtime.build_or_exit(config_exit_code=EXIT_CONFIG_ERROR)

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
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Named LLM provider profile (MANGOMAS_LLM_PROFILES__<NAME>__*) for this run",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Send a one-shot message to an agent and print the reply."""
    _runtime.configure_cli_logging(verbose=verbose)

    orch = _runtime.build_or_exit(config_exit_code=EXIT_CONFIG_ERROR)
    messages: list[Message] = []
    if system:
        messages.append(Message(role="system", content=system))
    messages.append(Message(role="user", content=message))

    # The selector rides request.metadata exactly as it does over HTTP
    # (spec-0040 R2); an unknown name is the same ConfigError the API maps to
    # 400, surfaced here on the dispatch path like any other config error.
    metadata = {LLM_PROFILE_METADATA_KEY: profile} if profile else {}
    request = AgentRequest(messages=messages, metadata=metadata)

    async def _run() -> str:
        try:
            response = await orch.dispatch(agent, request)
            return response.content
        finally:
            await _runtime._close_orchestrator(orch)

    typer.echo(
        _runtime.run_or_exit(
            _run(), failure_label="Chat failed", runtime_exit_code=EXIT_RUNTIME_ERROR
        )
    )


def history(
    # Bounded like the API's ``?limit=``: ``-1`` used to reach SQLite as
    # ``LIMIT -1`` (no limit at all) and dump every persisted turn.
    limit: int = typer.Option(
        DEFAULT_API_HISTORY_DEFAULT_LIMIT,
        "--limit",
        "-n",
        min=1,
        max=DEFAULT_API_HISTORY_MAX_LIMIT,
        help="Number of most recent turns to print",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Print recent persisted turns as JSON lines."""
    _runtime.configure_cli_logging(verbose=verbose)

    orch = _runtime.build_or_exit(config_exit_code=EXIT_CONFIG_ERROR)

    async def _run() -> list[dict[str, object]] | None:
        """Return rows, or ``None`` to signal "no repository configured"."""
        try:
            repo = orch.context.repo
            if repo is None:
                return None
            return await repo.list_turns(limit=limit)
        finally:
            await _runtime._close_orchestrator(orch)

    rows = _runtime.run_or_exit(
        _run(), failure_label="History failed", runtime_exit_code=EXIT_RUNTIME_ERROR
    )
    if rows is None:
        typer.echo("No repository configured.", err=True)
        raise typer.Exit(code=EXIT_RUNTIME_ERROR)

    for row in rows:
        typer.echo(json.dumps(row, ensure_ascii=False))
