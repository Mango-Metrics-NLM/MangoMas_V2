"""Unit tests for ``mangomas.utils.json_path`` helpers."""

from __future__ import annotations

import importlib
import importlib.util
import math
import numbers
from fractions import Fraction
from typing import Any

import pytest

from mangomas.utils.json_path import (
    coerce_finite_float,
    finite_number,
    is_bool_scalar,
    normalize_json_value,
    resolve_dotted_path,
    strict_json_equals,
)


def _make_fake_numpy_bool_type(type_name: str = "bool_") -> type[Any]:
    """Build a synthetic type with ``__module__ == 'numpy'`` for pure-P1 tests."""
    return type(type_name, (), {"__module__": "numpy"})


class _BrokenReal(numbers.Real):
    """Minimal ``numbers.Real`` whose ``__float__`` fails, covering conversion errors."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __float__(self) -> float:
        raise self._exc

    def __abs__(self) -> _BrokenReal:
        return self

    def __add__(self, other: object) -> _BrokenReal:
        return self

    def __ceil__(self) -> int:
        return 0

    def __eq__(self, other: object) -> bool:
        return False

    __hash__ = None  # type: ignore[assignment]

    def __floor__(self) -> int:
        return 0

    def __floordiv__(self, other: object) -> int:
        return 0

    def __le__(self, other: object) -> bool:
        return False

    def __lt__(self, other: object) -> bool:
        return False

    def __mod__(self, other: object) -> _BrokenReal:
        return self

    def __mul__(self, other: object) -> _BrokenReal:
        return self

    def __neg__(self) -> _BrokenReal:
        return self

    def __pos__(self) -> _BrokenReal:
        return self

    def __pow__(self, exponent: object) -> _BrokenReal:
        return self

    def __radd__(self, other: object) -> _BrokenReal:
        return self

    def __rfloordiv__(self, other: object) -> int:
        return 0

    def __rmod__(self, other: object) -> _BrokenReal:
        return self

    def __rmul__(self, other: object) -> _BrokenReal:
        return self

    def __round__(self, ndigits: int | None = None) -> Any:
        return 0

    def __rpow__(self, base: object) -> _BrokenReal:
        return self

    def __rtruediv__(self, other: object) -> float:
        return 0.0

    def __truediv__(self, other: object) -> float:
        return 0.0

    def __trunc__(self) -> int:
        return 0


def test_is_bool_scalar_recognizes_python_and_synthetic_numpy_bools() -> None:
    fake_bool_ = _make_fake_numpy_bool_type("bool_")()
    fake_bool = _make_fake_numpy_bool_type("bool")()
    assert is_bool_scalar(True) is True
    assert is_bool_scalar(False) is True
    assert is_bool_scalar(fake_bool_) is True
    assert is_bool_scalar(fake_bool) is True
    assert is_bool_scalar(1) is False
    assert is_bool_scalar(0) is False
    assert is_bool_scalar("true") is False


def test_finite_number_accepts_numbers_real_and_rejects_bool_overflow_and_non_finite() -> None:
    assert finite_number(3) == 3.0
    assert finite_number(-0.5) == -0.5
    assert finite_number(Fraction(1, 2)) == 0.5
    assert finite_number(True) is None
    assert finite_number("1.0") is None
    assert finite_number(float("nan")) is None
    assert finite_number(float("inf")) is None
    assert finite_number(float("-inf")) is None
    assert finite_number(10**400) is None
    assert finite_number(_BrokenReal(TypeError("bad"))) is None
    assert finite_number(_BrokenReal(ValueError("bad"))) is None
    assert finite_number(_BrokenReal(OverflowError("huge"))) is None


def test_coerce_finite_float_handles_strings_and_numbers() -> None:
    assert coerce_finite_float("0.25") == 0.25
    assert coerce_finite_float(" -3 ") == -3.0
    assert coerce_finite_float(2) == 2.0
    assert coerce_finite_float(True) is None
    assert coerce_finite_float("high") is None
    assert coerce_finite_float("nan") is None
    assert coerce_finite_float("inf") is None
    assert coerce_finite_float("1e500") is None
    assert coerce_finite_float(10**400) is None


def test_strict_json_equals_blocks_bool_numeric_coercion() -> None:
    assert strict_json_equals(True, True) is True
    assert strict_json_equals(False, False) is True
    assert strict_json_equals(True, 1) is False
    assert strict_json_equals(1, True) is False
    assert strict_json_equals(False, 0) is False
    assert strict_json_equals(1, 1.0) is True
    assert strict_json_equals("a", "b") is False


def test_resolve_dotted_path_walks_nested_mappings() -> None:
    payload = {"user": {"profile": {"score": 42}}}
    assert resolve_dotted_path(payload, "user.profile.score") == (True, 42)
    assert resolve_dotted_path(payload, ("user", "profile")) == (True, {"score": 42})
    assert resolve_dotted_path(payload, ()) == (True, payload)
    assert resolve_dotted_path(payload, "user.missing") == (False, None)
    assert resolve_dotted_path({"user": "leaf"}, "user.profile") == (False, None)


def test_real_numpy_scalars_when_installed() -> None:
    """Verify real NumPy scalars without triggering a skip in pure-P1 CI environments."""
    # Always verify the synthetic contract so the test is meaningful without optional extras.
    assert is_bool_scalar(_make_fake_numpy_bool_type("bool_")()) is True
    assert strict_json_equals(_make_fake_numpy_bool_type("bool")(), 1) is False
    if importlib.util.find_spec("numpy") is None:
        return
    np = importlib.import_module("numpy")
    assert is_bool_scalar(np.bool_(True)) is True
    assert finite_number(np.bool_(True)) is None
    assert strict_json_equals(np.bool_(True), 1) is False
    assert math.isclose(finite_number(np.float32(0.5)) or 0.0, 0.5)
    assert finite_number(np.int64(7)) == 7.0
    assert coerce_finite_float(np.float64(1.25)) == 1.25
    assert finite_number(np.float32("nan")) is None
    assert finite_number(np.float64("inf")) is None


def test_normalize_json_value_rejects_cyclic_containers() -> None:
    cyclic_list: list[Any] = []
    cyclic_list.append(cyclic_list)
    with pytest.raises(ValueError, match="Cyclic container"):
        normalize_json_value(cyclic_list)

    cyclic_dict: dict[str, Any] = {}
    cyclic_dict["self"] = cyclic_dict
    with pytest.raises(ValueError, match="Cyclic container"):
        normalize_json_value(cyclic_dict)
