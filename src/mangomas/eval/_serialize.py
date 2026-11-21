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
import json
import numbers
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mangomas.utils.json_path import finite_number, is_bool_scalar

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport


def report_payload(
    report: EvalReport,
    *,
    gate_result: GateResult | None = None,
) -> dict[str, Any]:
    """Serialise *report*, attaching *gate_result* under ``"gate"`` when present."""
    payload: dict[str, Any] = _finite_only(report)
    if gate_result is not None:
        payload["gate"] = _finite_only(gate_result)
    return payload


def _finite_scalar(value: Any) -> Any:
    """Normalize a single leaf scalar value for strict RFC-8259 JSON serialization."""
    if is_bool_scalar(value):
        return bool(value)
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return finite_number(value)
    return value


def finite_scalar(value: Any) -> Any:
    """Normalize a leaf scalar to a strict built-in JSON/SQLite-compatible scalar."""
    return _finite_scalar(value)


def _finite_only(value: Any) -> Any:
    """Replace non-finite numbers and normalize dataclasses/scalars/mappings for JSON.

    ``json.dumps`` defaults to ``allow_nan=True`` and would otherwise write the
    non-standard ``NaN`` / ``Infinity`` tokens into the ``json_file`` artefact
    (the baseline a later run gates against) and the webhook body, and
    ``dataclasses.asdict`` would ``copy.deepcopy`` read-only ``MappingProxyType``
    metadata and raise ``TypeError``.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _finite_only(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {k: _finite_only(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite_only(v) for v in value]
    return _finite_scalar(value)


def serialize_json(
    value: Any,
    *,
    ensure_ascii: bool = True,
    indent: int | None = None,
) -> str:
    """Normalize *value* and serialize it with strict ``allow_nan=False`` JSON."""
    return json.dumps(
        _finite_only(value), ensure_ascii=ensure_ascii, indent=indent, allow_nan=False
    )
