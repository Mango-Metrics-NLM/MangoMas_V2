"""Shared factory-time option-validation helpers for the eval registries.

Every sink / target / dataset-source factory resolves its constructor
arguments from a caller-supplied ``options: dict[str, Any]`` (the JSON blob
under ``MANGOMAS_EVAL__*_OPTIONS``, or a CLI-parsed dict). Before this module
existed, each factory duplicated one of three validation shapes — a required
non-empty string, a required list, or a threshold clamped to ``[0.0, 1.0]`` —
each raising :class:`~mangomas.errors.ConfigError` with near-identical
wording. A fourth helper, :func:`require_non_negative_float`, covers USD
amounts that must not be unit-clamped. This module is the single place those
shapes live; callers with genuinely bespoke validation (e.g. ``json_keys``'s
optional ``list | tuple`` ``required_keys``) are not forced onto these helpers.
"""

from __future__ import annotations

import numbers
from typing import Any

from mangomas.errors import ConfigError
from mangomas.utils.json_path import coerce_finite_float, finite_number


def require_str(options: dict[str, Any], key: str, *, owner: str) -> str:
    """Return ``options[key]`` as a non-empty ``str``.

    Raises :class:`ConfigError` when the key is missing, empty, or holds a
    non-``str`` value — no silent ``str()`` coercion. ``owner`` names the
    caller (e.g. ``"webhook sink"``) for the error message.
    """
    value = options.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{owner} requires a string '{key}' option")
    return value


def require_list(options: dict[str, Any], key: str, *, owner: str) -> list[Any]:
    """Return ``options[key]`` as a ``list``.

    Raises :class:`ConfigError` when the key is missing or holds a non-list
    value. Only the container type is checked — an empty list is valid here;
    callers that additionally require a *non-empty* list (e.g. the pipeline /
    fan_out targets' ``agents`` option) add that check themselves.
    """
    value = options.get(key)
    if not isinstance(value, list):
        raise ConfigError(f"{owner} requires a '{key}' list option")
    return value


def require_unit_float(value: float, *, owner: str, field: str = "threshold") -> float:
    """Return *value* if it lies within ``[0.0, 1.0]``.

    Raises :class:`ConfigError` otherwise. ``owner`` names the caller and
    ``field`` the option name (defaults to ``"threshold"``, the only current
    use case: ``LLMJudgeScorer`` / ``EmbeddingScorer``).
    """
    number = finite_number(value)
    if number is None or not 0.0 <= number <= 1.0:
        raise ConfigError(f"{owner} '{field}' must be in [0.0, 1.0]; got {value}")
    return number


def coerce_float(value: object, *, owner: str, field: str) -> float:
    """Return *value* as a finite ``float`` or raise :class:`ConfigError`.

    Accepts ``int`` / ``float`` / real numeric scalars (e.g. NumPy) and numeric
    strings (options arrive as JSON or env text), rejects ``bool``, overflows,
    non-finite values, and anything unparseable — so a typo such as
    ``"threshold": "high"`` is a config error (exit 2), not a bare
    ``ValueError`` or ``OverflowError`` escaping a factory.
    """
    number = coerce_finite_float(value)
    if number is None:
        raise ConfigError(f"{owner} '{field}' must be a number; got {value!r}")
    return number


def require_bool(options: dict[str, Any], key: str, *, default: bool, owner: str) -> bool:
    """Return ``options[key]`` as a ``bool``, defaulting when absent.

    Accepts real booleans and the case-insensitive strings ``"true"`` /
    ``"false"`` (the shapes an env-var JSON blob or CLI-parsed dict yields).
    Anything else raises :class:`ConfigError`. The previous ``bool(opts.get(...))``
    idiom read the string ``"false"`` as ``True``.
    """
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise ConfigError(f"{owner} '{key}' must be a boolean; got {value!r}")


def require_non_negative_float(value: object, *, owner: str, field: str) -> float:
    """Return *value* coerced to ``float`` when it is finite and ``>= 0.0``.

    Rejects ``bool`` (a subclass of ``int``) so ``True`` cannot silently
    become ``1.0``. Rejects ``NaN``, infinities, and overflowing ints.
    Used for USD amounts, which are not unit scores.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ConfigError(f"{owner} '{field}' must be a number; got {type(value).__name__}")
    number = finite_number(value)
    if number is None or number < 0.0:
        raise ConfigError(f"{owner} '{field}' must be a finite number >= 0.0; got {value!r}")
    return number
