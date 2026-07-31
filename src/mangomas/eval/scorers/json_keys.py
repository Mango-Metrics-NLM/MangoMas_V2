"""JSON-keys scorer — schema-conformance check for structured agent output.

Validates that ``prediction`` parses as a JSON object containing a set of
required keys. Directly useful for structured agents such as ``PlannerAgent``
(``ExecutionPlan``) and ``ReviewerAgent`` (``ReviewResult``).

Required keys are resolved as:

1. ``options["required_keys"]`` (a ``list[str]``) — **primary**.
2. otherwise, the keys of the per-row ``expected`` field parsed as a JSON object.

If neither yields keys (no option *and* ``expected`` is not a JSON object), the
scorer raises :class:`ConfigError` — that is misconfiguration, not a model
failure. A ``prediction`` that is not valid JSON is a legitimate *failing row*
(``score=0.0``), not a harness error.

Scoring is graded: ``score`` is the fraction of required keys present. The
optional ``strict`` flag (default ``False``) additionally forces ``score=0.0``
when the prediction carries any key beyond the required set. ``passed`` is
``score == 1.0``.
"""

from __future__ import annotations

import json
from typing import Any

from mangomas.errors import ConfigError
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry


class JsonKeysScorer:
    """Score structured JSON output by required-key coverage."""

    name = "json_keys"

    def __init__(self, *, required_keys: list[str] | None = None, strict: bool = False) -> None:
        self._required_keys = list(required_keys) if required_keys else None
        self._strict = strict

    def _resolve_required(self, expected: str) -> list[str]:
        if self._required_keys is not None:
            return self._required_keys
        try:
            parsed = json.loads(expected)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ConfigError(
                "json_keys scorer needs 'required_keys' option or a JSON-object "
                "'expected' field to derive keys from"
            ) from exc
        if not isinstance(parsed, dict):
            raise ConfigError("json_keys: 'expected' must be a JSON object to derive required keys")
        return list(parsed.keys())

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,  # noqa: ARG002
    ) -> ScoreResult:
        required = self._resolve_required(expected)
        try:
            parsed = json.loads(prediction)
        except (json.JSONDecodeError, TypeError):
            return ScoreResult(
                score=0.0,
                passed=False,
                metadata={"scorer": self.name, "error": "not_json"},
            )
        if not isinstance(parsed, dict):
            return ScoreResult(
                score=0.0,
                passed=False,
                metadata={"scorer": self.name, "error": "not_object"},
            )

        present = [k for k in required if k in parsed]
        score = len(present) / len(required) if required else 1.0
        extra_keys = [k for k in parsed if k not in required]
        if self._strict and extra_keys:
            score = 0.0
        missing = [k for k in required if k not in parsed]
        return ScoreResult(
            score=score,
            passed=score == 1.0,
            metadata={
                "scorer": self.name,
                "required": required,
                "missing": missing,
                "extra": extra_keys,
                "strict": self._strict,
            },
        )


def _json_keys_factory(options: dict[str, Any]) -> Scorer:
    raw = options.get("required_keys")
    if raw is not None and not isinstance(raw, (list, tuple)):
        raise ConfigError("json_keys: 'required_keys' option must be a list or tuple")
    required = [str(k) for k in raw] if raw is not None else None
    return JsonKeysScorer(required_keys=required, strict=bool(options.get("strict", False)))


scorer_registry.register("json_keys", _json_keys_factory)
