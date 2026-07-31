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
        )
        for row in report.rows:
            flag = "PASS" if row.passed else "FAIL"
            self._write(f"  [{flag}] {row.row_id} score={row.score:.3f}")
        if gate_result is not None:
            verdict = "PASS" if gate_result.passed else "FAIL"
            suffix = "" if gate_result.passed else f" ({'; '.join(gate_result.reasons)})"
            self._write(f"gate={verdict}{suffix}")


def _console_factory(options: dict[str, Any]) -> Sink:  # noqa: ARG001 — no options today
    return ConsoleSink()


sink_registry.register("console", _console_factory)
