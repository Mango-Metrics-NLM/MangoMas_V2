"""Shared JSON-path traversal and NumPy-safe finite-numeric helpers.

Pure stdlib helpers shared across independent domain layers (e.g.
``mangomas.eval`` and ``mangomas.workflow.predicate``) without violating
import-linter layer isolation boundaries.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping, Sequence
from typing import Any


def is_bool_scalar(value: object) -> bool:
    """Return ``True`` for Python ``bool`` and NumPy boolean scalars."""
    if isinstance(value, bool):
        return True
    value_type = type(value)
    return (
        not isinstance(value, numbers.Real)
        and value_type.__module__.startswith("numpy")
        and value_type.__name__ in ("bool", "bool_")
    )


def finite_number(value: object) -> float | None:
    """Convert numeric *value* to a finite ``float``, or return ``None``.

    Rejects Python ``bool`` (a subclass of ``int``) and boolean scalars (such as
    ``numpy.bool_``) while accepting ``int``, ``float``, and real scalar types
    implementing :class:`numbers.Real` (e.g. ``numpy.float32``, ``numpy.int64``).
    Returns ``None`` on overflow or non-finite values (``NaN``, ``±Inf``).
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def coerce_finite_float(value: object) -> float | None:
    """Convert a real scalar or numeric string to a finite ``float``, or ``None``.

    Rejects ``bool`` / NumPy boolean scalars, unparseable strings, overflows,
    and non-finite values (``NaN``, ``±Inf``).
    """
    if isinstance(value, str):
        try:
            return finite_number(float(value))
        except (OverflowError, ValueError):
            return None
    return finite_number(value)


def strict_json_equals(found: object, expected: object) -> bool:
    """Compare two parsed values without Python/NumPy ``True == 1`` coercion."""
    if is_bool_scalar(found) is not is_bool_scalar(expected):
        return False
    return bool(found == expected)


def resolve_dotted_path(data: Mapping[str, Any], path: str | Sequence[str]) -> tuple[bool, Any]:
    """Walk *path* over nested mappings. Returns ``(False, None)`` if unresolved."""
    segments: Sequence[str] = path.split(".") if isinstance(path, str) else path
    current: Any = data
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current
