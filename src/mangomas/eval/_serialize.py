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
import math
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
    sanitised: dict[str, Any] = _finite_only(payload)
    return sanitised


def _finite_only(value: Any) -> Any:
    """Replace non-finite floats with ``None`` throughout a JSON-ish tree.

    ``json.dumps`` defaults to ``allow_nan=True`` and would otherwise write the
    non-standard ``NaN`` / ``Infinity`` tokens into the ``json_file`` artefact
    (the baseline a later run gates against) and the webhook body.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite_only(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite_only(v) for v in value]
    return value
