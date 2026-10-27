"""Factory-time option helpers shared by the eval registries."""

from __future__ import annotations

import pytest

import mangomas.eval.scorers  # noqa: F401 — registers built-in scorers
from mangomas.errors import ConfigError
from mangomas.eval._options import coerce_float, require_bool
from mangomas.eval.registry import scorer_registry


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", True), ("False", False), (" TRUE ", True)],
)
def test_require_bool_accepts_bools_and_their_strings(value: object, expected: bool) -> None:
    assert require_bool({"k": value}, "k", default=not expected, owner="t") is expected


def test_require_bool_uses_default_when_absent() -> None:
    assert require_bool({}, "k", default=True, owner="t") is True


@pytest.mark.parametrize("value", ["yes", 1, 0, None, "", [True]])
def test_require_bool_rejects_everything_else(value: object) -> None:
    with pytest.raises(ConfigError, match="'k' must be a boolean"):
        require_bool({"k": value}, "k", default=False, owner="t")


@pytest.mark.parametrize("value", [0.5, 1, "0.25"])
def test_coerce_float_accepts_numbers_and_numeric_strings(value: object) -> None:
    assert isinstance(coerce_float(value, owner="t", field="f"), float)


@pytest.mark.parametrize("value", [True, "high", None, [1]])
def test_coerce_float_rejects_non_numbers(value: object) -> None:
    with pytest.raises(ConfigError, match="'f' must be a number"):
        coerce_float(value, owner="t", field="f")


@pytest.mark.parametrize(
    ("scorer", "key"),
    [
        ("exact_match", "case_sensitive"),
        ("exact_match", "strip_whitespace"),
        ("contains", "case_sensitive"),
        ("regex_match", "fullmatch"),
        ("json_keys", "strict"),
    ],
)
def test_scorer_factories_read_the_string_false_as_false(scorer: str, key: str) -> None:
    """``bool(opts.get(...))`` read ``"false"`` as ``True``; every factory now goes
    through ``require_bool``. Mutation: restore ``bool(...)`` in one factory ->
    that parametrisation fails."""
    instance = scorer_registry.get(scorer)({key: "false"})
    assert getattr(instance, f"_{key}") is False


def test_scorer_factories_reject_a_non_boolean_option() -> None:
    with pytest.raises(ConfigError, match="must be a boolean"):
        scorer_registry.get("contains")({"case_sensitive": "sometimes"})
