"""Console sink — the human-readable stdout summary.

Reproduces, byte-for-byte, the inline summary the CLI printed before sinks
existed (a header line plus one ``[PASS|FAIL]`` line per row), and adds a
``gate=...`` line when a :class:`GateResult` is supplied. The writer is
injectable (default :func:`typer.echo`, which handles newline/encoding) so
tests can capture output without monkeypatching.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import typer

from mangomas.eval.sink import Sink
from mangomas.eval.sink_registry import sink_registry
from mangomas.eval.stats import wilson_interval

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport


class ConsoleSink:
    """Write the eval summary to a line-writer (default ``typer.echo``)."""

    name = "console"

    def __init__(self, *, write: Callable[[str], None] = typer.echo) -> None:
        self._write = write

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        self._write(
            f"scorer={report.scorer} agent={report.agent_name} "
            f"size={report.dataset_size} passed={report.passed} "
            f"failed={report.failed} errored={report.errored} "
            f"mean_score={report.mean_score:.3f}"
            + (
                f" mean_cost_usd={report.mean_cost_usd:.6f}"
                if report.mean_cost_usd is not None
                else ""
            )
        )
        if report.dataset_size:
            low, high = wilson_interval(report.passed, report.dataset_size)
            self._write(
                f"pass_rate={report.passed / report.dataset_size:.3f} "
                f"(95% Wilson {low:.3f}-{high:.3f}, informational; n={report.dataset_size})"
            )
        if report.trials > 1 and report.pass_pow_k is not None:
            per_trial = (
                f" trial_pass_rate={report.trial_pass_rate:.3f}"
                if report.trial_pass_rate is not None
                else ""
            )
            self._write(f"trials={report.trials} pass_pow_k={report.pass_pow_k:.3f}{per_trial}")
        for name, stats in report.slices.items():
            self._write(
                f"  slice={name} size={stats.size} passed={stats.passed} "
                f"failed={stats.failed} errored={stats.errored} mean_score={stats.mean_score:.3f}"
            )
        for row in report.rows:
            flag = "PASS" if row.passed else "FAIL"
            self._write(f"  [{flag}] {row.row_id} score={row.score:.3f}")
        if report.run is not None:
            sha = report.run.git_sha or "unknown"
            self._write(
                f"run={report.run.run_id} dataset={report.run.dataset_sha256[:19]} git={sha[:12]}"
            )
        if gate_result is not None:
            verdict = "PASS" if gate_result.passed else "FAIL"
            suffix = "" if gate_result.passed else f" ({'; '.join(gate_result.reasons)})"
            self._write(f"gate={verdict}{suffix}")


def _console_factory(options: dict[str, Any]) -> Sink:  # noqa: ARG001 — no options today
    return ConsoleSink()


sink_registry.register("console", _console_factory)
