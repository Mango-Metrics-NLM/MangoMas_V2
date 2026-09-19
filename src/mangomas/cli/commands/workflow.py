"""The `mangomas workflow` sub-app: `validate` and `run`.

Same shape as `rag`: the sub-app and its decorators are local, so the
`validate, run` listing order is statement order in this file, and `_app` only
adds the group.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import typer

from mangomas.cli import _runtime
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from mangomas.composition.agents import STRUCTURED_AGENT_FIELDS
from mangomas.config import get_settings
from mangomas.core import AgentRequest, Message
from mangomas.errors import MangomasError
from mangomas.workflow import execute_workflow, load_workflow, resolve_workflow_source

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.workflow import WorkflowGraph

workflow_app = typer.Typer(
    help="Declarative multi-agent workflow-graph commands", no_args_is_help=True
)


def _resolve_workflow_source(definition: str | None) -> str:
    """Return the effective graph source, or ``Exit(2)`` when off/unset.

    Delegates the opt-in precedence rule to the shared
    :func:`mangomas.workflow.resolve_workflow_source` (single source of truth
    with the HTTP surface) and maps its :class:`ConfigError` onto the CLI's
    config exit code (2), mirroring :func:`_load_workflow_or_exit`.
    """
    try:
        return resolve_workflow_source(definition, get_settings().workflow)
    except MangomasError as exc:
        typer.echo(f"Workflow configuration error: {exc}", err=True)
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from exc


def _load_workflow_or_exit(source: str) -> WorkflowGraph:
    """Parse *source* into a graph, mapping a config error to ``Exit(2)``.

    Validates acceptance predicates against the **built-in** structured agents
    (:data:`~mangomas.composition.agents.STRUCTURED_AGENT_FIELDS`), not the
    plugin-inclusive map ``build_orchestrator`` publishes. Both CLI commands load
    the graph *before* building an orchestrator, deliberately: a malformed graph
    then exits 2 without paying for storage and LLM wiring. Keeping that
    fail-fast ordering costs coverage of an entry-point structured agent here,
    which is the narrower surface — the HTTP routes, which accept a
    caller-supplied graph, always have an orchestrator in ``app.state`` and use
    its full map.
    """
    try:
        return load_workflow(source, structured_agents=STRUCTURED_AGENT_FIELDS)
    except MangomasError as exc:
        typer.echo(f"Workflow configuration error: {exc}", err=True)
        raise typer.Exit(code=EXIT_CONFIG_ERROR) from exc


@workflow_app.command(name="validate")
def workflow_validate(
    definition: str | None = typer.Option(
        None, "--definition", "-f", help="Path or inline JSON graph (falls back to settings)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Parse and validate a workflow graph without running it (no LLM I/O)."""
    _runtime.configure_cli_logging(verbose=verbose)

    graph = _load_workflow_or_exit(_resolve_workflow_source(definition))
    typer.echo(f"ok name={graph.name} root={graph.root.kind}")


@workflow_app.command(name="run")
def workflow_run(
    message: str = typer.Argument(..., help="User message fed to the graph's root node"),
    definition: str | None = typer.Option(
        None, "--definition", "-f", help="Path or inline JSON graph (falls back to settings)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Execute a declarative workflow graph and print the final node's response."""
    _runtime.configure_cli_logging(verbose=verbose)

    graph = _load_workflow_or_exit(_resolve_workflow_source(definition))
    orch = _runtime._build()
    request = AgentRequest(messages=[Message(role="user", content=message)])

    async def _run() -> str:
        try:
            response = await execute_workflow(graph, request, orch=orch)
            return response.content
        finally:
            await _runtime._close_orchestrator(orch)

    try:
        typer.echo(asyncio.run(_run()))
    except MangomasError as exc:
        typer.echo(f"Workflow run failed: {exc}", err=True)
        raise typer.Exit(code=EXIT_RUNTIME_ERROR) from exc
