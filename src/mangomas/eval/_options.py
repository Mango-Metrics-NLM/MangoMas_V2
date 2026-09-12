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

import math
from typing import Any

from mangomas.errors import ConfigError


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
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{owner} '{field}' must be in [0.0, 1.0]; got {value}")
    return value


def require_non_negative_float(value: object, *, owner: str, field: str) -> float:
    """Return *value* coerced to ``float`` when it is finite and ``>= 0.0``.

    Rejects ``bool`` (a subclass of ``int``) so ``True`` cannot silently
    become ``1.0``. Rejects ``NaN`` / infinities: those compare false
    against ``>= 0.0`` / ``>`` and would otherwise bypass a USD cap.
    Used for USD amounts, which are not unit scores.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{owner} '{field}' must be a number; got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ConfigError(f"{owner} '{field}' must be a finite number >= 0.0; got {number}")
    return number
