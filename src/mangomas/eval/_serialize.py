"""Shared ``EvalReport`` → dict serialisation for sinks and baselines.

The ``json_file`` and ``webhook`` sinks must emit the *same* payload shape —
``dataclasses.asdict(report)`` plus a top-level ``"gate"`` key when a verdict
is present — and the baseline loader round-trips that shape back into an
:class:`~mangomas.eval.runner.EvalReport`. Hosting the single payload builder
here makes the invariant structural instead of prose in a docstring (the same
drift-prevention move as :mod:`mangomas.adapters._http_errors`).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport


def report_payload(
    report: EvalReport,
    *,
    gate_result: GateResult | None = None,
) -> dict[str, Any]:
    """Serialise *report*, attaching *gate_result* under ``"gate"`` when present."""
    payload: dict[str, Any] = dataclasses.asdict(report)
    if gate_result is not None:
        payload["gate"] = dataclasses.asdict(gate_result)
    return payload
