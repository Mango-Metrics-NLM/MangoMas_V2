"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import TYPE_CHECKING

import typer

import mangomas.eval.scorers  # registers built-in scorers (side-effect import)
import mangomas.eval.sinks
import mangomas.eval.sources
import mangomas.eval.targets  # noqa: F401 — registers built-in targets
from mangomas.composition import build_orchestrator
from mangomas.config import get_settings
from mangomas.core import AgentRequest, Message
from mangomas.errors import ConfigError, MangomasError
from mangomas.eval import (
    EvalReport,
    EvalRunner,
    dataset_source_registry,
    ensure_eval_plugins,
    evaluate_gate,
    scorer_registry,
    sink_registry,
    target_registry,
)
from mangomas.rag import IngestionPipeline, IngestReport, Retriever

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.config import EvalSettings
    from mangomas.core import Orchestrator
    from mangomas.eval import DatasetSource, GateResult, Sink, Target

# Windows default console codec is cp1252; LLM replies routinely contain
# em-dashes, smart quotes, etc. that cp1252 cannot encode, which crashes
# typer.echo. Reconfigure to UTF-8 with replacement so output never crashes.
if sys.platform == "win32":  # pragma: no cover — platform-gated
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help="Mango-Mas V2 CLI", no_args_is_help=True)

logger = logging.getLogger(__name__)


def _build() -> Orchestrator:
    """Construct the orchestrator with full settings + adapter wiring."""
    return build_orchestrator()


async def _close_orchestrator(orch: Orchestrator) -> None:
    """Release adapter resources cleanly via :meth:`Orchestrator.aclose`.

    Retained as a thin wrapper so existing tests can monkeypatch the CLI's
    close path without reaching into core. The real teardown logic lives on
    :class:`~mangomas.core.Orchestrator` so every entry point (CLI, FastAPI
    lifespan, demo scripts) shares one tested code path.
    """
    await orch.aclose()


@app.command()
def agents() -> None:
    """List registered agents."""
    orch = _build()

    async def _run() -> list[str]:
        try:
            return list(orch.list_agents())
        finally:
            await _close_orchestrator(orch)

    for name in asyncio.run(_run()):
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

    async def _run() -> str:
        try:
            response = await orch.dispatch(agent, request)
            return response.content
        finally:
            await _close_orchestrator(orch)

    typer.echo(asyncio.run(_run()))


@app.command()
def history(
    limit: int = typer.Option(10, "--limit", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Print recent persisted turns as JSON lines."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    orch = _build()

    async def _run() -> list[dict[str, object]] | None:
        """Return rows, or ``None`` to signal "no repository configured"."""
        try:
            repo = orch.context.repo
            if repo is None:
                return None
            return await repo.list_turns(limit=limit)
        finally:
            await _close_orchestrator(orch)

    rows = asyncio.run(_run())
    if rows is None:
        typer.echo("No repository configured.", err=True)
        raise typer.Exit(code=1)

    for row in rows:
        typer.echo(json.dumps(row, ensure_ascii=False))


# Exit code raised when the quality gate fails — distinct from 1 (runtime) and
# 2 (config) so CI can react specifically to a quality regression.
EVAL_GATE_EXIT_CODE = 3


def _build_sinks(
    sink_names: list[str],
    sink_options: dict[str, dict[str, object]],
    output_json: str | None,
) -> list[Sink]:
    """Resolve sink names to instances, honouring the legacy ``--output-json``.

    ``--output-json`` injects (or overrides the ``path`` of) the ``json_file``
    sink so pre-existing invocations keep producing the same file. CLI flag
    precedence: ``--output-json`` > ``sink_options["json_file"]["path"]``. Any
    *other* configured ``sink_options["json_file"]`` keys are preserved even
    when the sink is injected solely by ``--output-json``.
    Raises :class:`~mangomas.errors.MangomasError` (→ exit 2) on an unknown sink
    or a misconfigured option.
    """
    names = list(sink_names)
    options: dict[str, dict[str, object]] = {n: dict(sink_options.get(n, {})) for n in names}
    if output_json:
        if "json_file" not in names:
            names.append("json_file")
        # Seed from any configured json_file options so injecting the sink via
        # --output-json never silently drops them; only ``path`` is overridden.
        options.setdefault("json_file", dict(sink_options.get("json_file", {})))
        options["json_file"]["path"] = output_json
    return [sink_registry.get(name)(options.get(name, {})) for name in names]


def _build_target(
    target_name: str,
    target_options: dict[str, dict[str, object]],
    agent_flag: str | None,
    default_agent: str,
) -> Target:
    """Resolve a target name to an instance, folding in the agent selection.

    For the ``agent`` target the agent name is sourced with precedence
    ``--agent`` flag > ``target_options["agent"]["agent"]`` > settings default,
    so existing ``--agent X`` invocations keep selecting the agent. Other
    targets read their config straight from ``target_options``. Raises
    :class:`~mangomas.errors.MangomasError` (→ exit 2) on an unknown target or a
    misconfigured option.
    """
    options = dict(target_options.get(target_name, {}))
    if target_name == "agent":
        if agent_flag is not None:
            options["agent"] = agent_flag
        else:
            options.setdefault("agent", default_agent)
    return target_registry.get(target_name)(options)


def _build_dataset_source(
    source_name: str,
    source_options: dict[str, dict[str, object]],
    dataset_flag: str | None,
    default_path: str | None,
) -> DatasetSource:
    """Resolve a dataset source name to an instance, folding in the JSONL path.

    For the ``jsonl`` source the path is sourced with precedence ``--dataset``
    flag > ``dataset_source_options["jsonl"]["path"]`` > settings ``dataset_path``,
    so existing ``--dataset`` invocations keep working. Other sources read their
    config straight from ``dataset_source_options``. Raises
    :class:`~mangomas.errors.MangomasError` (→ exit 2) on an unknown source or a
    missing/misconfigured option (e.g. no JSONL path).
    """
    options = dict(source_options.get(source_name, {}))
    if source_name == "jsonl":
        path = dataset_flag or options.get("path") or default_path
        if not path:
            raise ConfigError(
                "No dataset path provided. Pass --dataset or set MANGOMAS_EVAL__DATASET_PATH."
            )
        options["path"] = path
    return dataset_source_registry.get(source_name)(options)


async def _emit_sinks(
    sinks: list[Sink],
    report: EvalReport,
    gate_result: GateResult | None,
) -> BaseException | None:
    """Emit *report* to every sink under per-sink fault isolation.

    A failure in one sink is logged and does not prevent the others; the first
    exception is returned so the caller can surface a non-zero exit *after*
    every sink has been attempted (so partial artifacts still land).
    """
    first_exc: BaseException | None = None
    for sink in sinks:
        try:
            await sink.emit(report, gate_result=gate_result)
        except Exception as exc:
            logger.exception("Eval sink %r failed", sink.name)
            first_exc = first_exc or exc
    return first_exc


def _resolve_gating(
    *,
    gate: bool | None,
    min_mean_score: float | None,
    min_pass_rate: float | None,
    fail_on_error: bool | None,
    cfg: EvalSettings,
) -> tuple[bool, float | None, float | None, bool]:
    """Resolve effective gate config (CLI over settings) and validate thresholds.

    Returns ``(gating_engaged, min_mean, min_pass, fail_on_error)``. An explicit
    ``--no-gate`` disables gating even when thresholds / ``fail_on_error`` are
    configured via settings/env. Raises ``typer.Exit(2)`` on an out-of-range
    threshold (these bypass the ``EvalSettings`` validator) — but only when
    gating is engaged, since an unused threshold should not block a run.
    """
    eff_min_mean = min_mean_score if min_mean_score is not None else cfg.min_mean_score
    eff_min_pass = min_pass_rate if min_pass_rate is not None else cfg.min_pass_rate
    eff_fail_on_error = fail_on_error if fail_on_error is not None else cfg.fail_on_error
    gate_enabled = gate if gate is not None else cfg.gate_enabled
    if gate is False:
        gating_engaged = False
    else:
        gating_engaged = (
            gate_enabled
            or eff_min_mean is not None
            or eff_min_pass is not None
            or eff_fail_on_error
        )

    if gating_engaged:
        for label, val in (("min-mean-score", eff_min_mean), ("min-pass-rate", eff_min_pass)):
            if val is not None and not 0.0 <= val <= 1.0:
                typer.echo(
                    f"Eval configuration error: {label} must be in [0.0, 1.0]; got {val}",
                    err=True,
                )
                raise typer.Exit(code=2)
    return gating_engaged, eff_min_mean, eff_min_pass, eff_fail_on_error


@app.command(name="eval")
def eval_cmd(
    dataset_path: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="JSONL dataset path (jsonl source); falls back to MANGOMAS_EVAL__DATASET_PATH.",
    ),
    dataset_source: str | None = typer.Option(
        None,
        "--dataset-source",
        help="Dataset source: jsonl|inline|langfuse (default from MANGOMAS_EVAL__DATASET_SOURCE).",
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
        help="Agent name for the 'agent' target (default from MANGOMAS_EVAL__AGENT).",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Target name: agent|pipeline|fan_out|echo (default from MANGOMAS_EVAL__TARGET).",
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
        help="If set, write a JSON report to this path (injects the json_file sink).",
    ),
    gate: bool | None = typer.Option(
        None,
        "--gate/--no-gate",
        help="Enable/disable the quality gate (default from MANGOMAS_EVAL__GATE_ENABLED).",
    ),
    min_mean_score: float | None = typer.Option(
        None,
        "--min-mean-score",
        help="Fail (exit 3) if mean_score < this threshold.",
    ),
    min_pass_rate: float | None = typer.Option(
        None,
        "--min-pass-rate",
        help="Fail (exit 3) if pass_rate < this threshold.",
    ),
    fail_on_error: bool | None = typer.Option(
        None,
        "--fail-on-error/--no-fail-on-error",
        help="Fail the gate (exit 3) if any row errored.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Run the configured scorer over a JSONL dataset, emit to sinks, and gate."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    settings = get_settings()
    cfg = settings.eval
    # Layer in any entry-point scorer/sink/target/source plugins (no-op unless enabled).
    ensure_eval_plugins(settings)

    scorer_name = scorer or cfg.scorer
    target_name = target or cfg.target
    source_name = dataset_source or cfg.dataset_source
    effective_parallelism = parallelism if parallelism is not None else cfg.parallelism
    effective_fail_fast = fail_fast if fail_fast is not None else cfg.fail_fast

    gating_engaged, eff_min_mean, eff_min_pass, eff_fail_on_error = _resolve_gating(
        gate=gate,
        min_mean_score=min_mean_score,
        min_pass_rate=min_pass_rate,
        fail_on_error=fail_on_error,
        cfg=cfg,
    )

    # Build scorer + sinks up front so misconfiguration fails fast (exit 2)
    # before a (potentially long) eval run.
    try:
        scorer_factory = scorer_registry.get(scorer_name)
        scorer_instance = scorer_factory(dict(cfg.scorer_options))
        sinks = _build_sinks(list(cfg.sinks), cfg.sink_options, output_json)
        target_instance = _build_target(target_name, cfg.target_options, agent, cfg.agent)
        source_instance = _build_dataset_source(
            source_name, cfg.dataset_source_options, dataset_path, cfg.dataset_path
        )
    except MangomasError as exc:
        detail = f" ({exc.detail})" if exc.detail else ""
        typer.echo(f"Eval configuration error: {exc}{detail}", err=True)
        raise typer.Exit(code=2) from exc

    orch = _build()
    runner = EvalRunner(
        orch,
        scorer_instance,
        parallelism=effective_parallelism,
        fail_fast=effective_fail_fast,
    )

    async def _run_eval() -> tuple[EvalReport, GateResult | None, BaseException | None]:
        try:
            dataset = await source_instance.load()
            report = await runner.run(dataset, target=target_instance)
        finally:
            await _close_orchestrator(orch)
        gate_result = (
            evaluate_gate(
                report,
                min_mean_score=eff_min_mean,
                min_pass_rate=eff_min_pass,
                fail_on_error=eff_fail_on_error,
            )
            if gating_engaged
            else None
        )
        sink_error = await _emit_sinks(sinks, report, gate_result)
        return report, gate_result, sink_error

    try:
        _report, gate_result, sink_error = asyncio.run(_run_eval())
    except MangomasError as exc:
        typer.echo(f"Eval run failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if sink_error is not None:
        typer.echo(f"Eval sink error: {sink_error}", err=True)
        raise typer.Exit(code=1)
    # Preserve the pre-sink-refactor confirmation line so existing
    # ``--output-json`` scripts still see "Report written to ...".
    if output_json:
        typer.echo(f"Report written to {output_json}")
    if gate_result is not None and not gate_result.passed:
        raise typer.Exit(code=EVAL_GATE_EXIT_CODE)


rag_app = typer.Typer(help="Retrieval-augmented generation commands", no_args_is_help=True)
app.add_typer(rag_app, name="rag")


def _require_rag(orch: Orchestrator) -> tuple[object, object]:
    """Return ``(embeddings, vector_store)`` or exit(2) if RAG is not enabled."""
    ctx = orch.context
    if ctx.embeddings is None or ctx.vector_store is None:
        typer.echo(
            "RAG is not enabled. Set MANGOMAS_EMBEDDINGS__ENABLED=true and "
            "MANGOMAS_VECTOR__ENABLED=true.",
            err=True,
        )
        raise typer.Exit(code=2)
    return ctx.embeddings, ctx.vector_store


@rag_app.command(name="ingest")
def rag_ingest(
    path: str = typer.Argument(..., help="File or directory of *.txt / *.md to ingest"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging"),
) -> None:
    """Chunk, embed and upsert documents into the vector store."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = get_settings()
    orch = _build()

    async def _run() -> IngestReport:
        try:
            embeddings, vector_store = _require_rag(orch)
            pipeline = IngestionPipeline(
                embeddings=embeddings,  # type: ignore[arg-type]
                vector_store=vector_store,  # type: ignore[arg-type]
                settings=cfg.rag,
                batch_size=cfg.embeddings.batch_size,
            )
            return await pipeline.ingest(path)
        finally:
            await _close_orchestrator(orch)

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
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = get_settings()
    orch = _build()

    async def _run() -> list[str]:
        try:
            embeddings, vector_store = _require_rag(orch)
            retriever = Retriever(
                embeddings=embeddings,  # type: ignore[arg-type]
                vector_store=vector_store,  # type: ignore[arg-type]
                top_k=cfg.vector.top_k,
            )
            results = await retriever.search(text, top_k=top_k)
            return [
                f"[{i + 1}] score={r.score:.3f} source={r.chunk.source}\n{r.chunk.text}"
                for i, r in enumerate(results)
            ]
        finally:
            await _close_orchestrator(orch)

    lines = asyncio.run(_run())
    if not lines:
        typer.echo("No relevant context found.")
        return
    for line in lines:
        typer.echo(line)


if __name__ == "__main__":  # pragma: no cover
    app()
