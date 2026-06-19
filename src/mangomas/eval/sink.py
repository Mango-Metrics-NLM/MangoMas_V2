"""Sink protocol — a destination for a finished :class:`EvalReport`.

Sinks decouple *producing* an evaluation report from *emitting* it (stdout,
JSON file, Langfuse, ...). The CLI builds one or more sinks from
``EvalSettings.sinks`` and emits to each under fault isolation, so one failing
sink never costs the others their output.

``emit`` is async because real sinks perform I/O (file writes, SDK calls) which
must run off the event loop via ``asyncio.to_thread`` per project convention.
The optional ``gate_result`` is the only channel for quality-gate context —
:class:`~mangomas.eval.runner.EvalReport` stays a frozen, unchanged contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport


@runtime_checkable
class Sink(Protocol):
    """A named destination for an evaluation report."""

    name: str

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        """Emit *report* (and optional *gate_result*) to this sink."""
        ...
