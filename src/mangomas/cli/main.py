"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import typer

import mangomas.eval.scorers  # noqa: F401 — registers built-in scorers
from mangomas.composition import build_orchestrator
from mangomas.config import get_settings
from mangomas.core import AgentRequest, Message
from mangomas.errors import MangomasError
from mangomas.eval import EvalRunner, load_jsonl, scorer_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.core import Orchestrator

app = typer.Typer(help="Mango-Mas V2 CLI", no_args_is_help=True)

logger = logging.getLogger(__name__)


def _build() -> Orchestrator:
    """Construct the orchestrator with full settings + adapter wiring."""
    return build_orchestrator()


@app.command()
def agents() -> None:
    """List registered agents."""
    orch = _build()
    for name in orch.list_agents():
        typer.echo(name)


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
    response = asyncio.run(orch.dispatch(agent, request))
    typer.echo(response.content)


@app.command()
def history(
    limit: int = typer.Option(10, "--limit", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Print recent persisted turns as JSON lines."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    orch = _build()
    repo = orch.context.repo
    if repo is None:
        typer.echo("No repository configured.", err=True)
        raise typer.Exit(code=1)
    rows = asyncio.run(repo.list_turns(limit=limit))
    for row in rows:
        typer.echo(json.dumps(row, ensure_ascii=False))


@app.command(name="eval")
def eval_cmd(
    dataset_path: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="JSONL dataset path; falls back to MANGOMAS_EVAL__DATASET_PATH.",
    ),
    scorer: str | None = typer.Option(
        None,
        "--scorer",
        "-s",
        help="Scorer name (default from MANGOMAS_EVAL__SCORER).",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Agent name (default from MANGOMAS_EVAL__AGENT).",
    ),
    parallelism: int | None = typer.Option(
        None,
        "--parallelism",
        "-p",
        help="Max concurrent rows (default from MANGOMAS_EVAL__PARALLELISM).",
    ),
    fail_fast: bool | None = typer.Option(
        None,
        "--fail-fast/--no-fail-fast",
        help="Cancel remaining rows after the first non-pass.",
    ),
    output_json: str | None = typer.Option(
        None,
        "--output-json",
        "-o",
        help="If set, write a JSON report to this path.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Run the configured scorer over a JSONL dataset and print a summary."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = get_settings().eval
    effective_dataset = dataset_path or cfg.dataset_path
    if not effective_dataset:
        typer.echo(
            "No dataset path provided. Pass --dataset or set MANGOMAS_EVAL__DATASET_PATH.",
            err=True,
        )
        raise typer.Exit(code=2)

    scorer_name = scorer or cfg.scorer
    agent_name = agent or cfg.agent
    effective_parallelism = parallelism if parallelism is not None else cfg.parallelism
    effective_fail_fast = fail_fast if fail_fast is not None else cfg.fail_fast

    try:
        scorer_factory = scorer_registry.get(scorer_name)
    except MangomasError as exc:
        typer.echo(f"Unknown scorer {scorer_name!r}: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    scorer_instance = scorer_factory(dict(cfg.scorer_options))

    orch = _build()
    runner = EvalRunner(
        orch,
        scorer_instance,
        parallelism=effective_parallelism,
        fail_fast=effective_fail_fast,
    )

    try:
        dataset = asyncio.run(load_jsonl(effective_dataset))
        report = asyncio.run(runner.run(dataset, agent_name))
    except MangomasError as exc:
        typer.echo(f"Eval run failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"scorer={report.scorer} agent={report.agent_name} "
        f"size={report.dataset_size} passed={report.passed} "
        f"failed={report.failed} errored={report.errored} "
        f"mean_score={report.mean_score:.3f}"
    )
    for row in report.rows:
        flag = "PASS" if row.passed else "FAIL"
        typer.echo(f"  [{flag}] {row.row_id} score={row.score:.3f}")

    if output_json:
        out_path = Path(output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dataclasses.asdict(report)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        typer.echo(f"Report written to {out_path}")


if __name__ == "__main__":  # pragma: no cover
    app()
