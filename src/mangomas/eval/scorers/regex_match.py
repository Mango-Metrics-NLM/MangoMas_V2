"""Regular-expression scorer.

``score == 1.0`` iff the per-row ``expected`` field — interpreted as a regular
expression — matches ``prediction``. Note the deliberate overload: for this
scorer ``expected`` is a *pattern*, not a gold answer (unlike ``exact_match`` /
``llm_judge``). Keep regex datasets separate from exact-match datasets.

Options (via ``EvalSettings.scorer_options``):

``flags`` (list[str], default ``[]``)
    Any of ``ignorecase`` / ``multiline`` / ``dotall`` — OR-combined.
``fullmatch`` (bool, default ``False``)
    Require the pattern to match the entire prediction (``re.fullmatch``)
    rather than anywhere within it (``re.search``).
"""

from __future__ import annotations

import re
from typing import Any

from mangomas.errors import ConfigError
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry

_FLAG_BY_NAME: dict[str, re.RegexFlag] = {
    "ignorecase": re.IGNORECASE,
    "multiline": re.MULTILINE,
    "dotall": re.DOTALL,
}


def _resolve_flags(names: list[str]) -> int:
    flags = 0
    for name in names:
        flag = _FLAG_BY_NAME.get(name.lower())
        if flag is None:
            raise ConfigError(f"unknown regex flag {name!r}; valid: {sorted(_FLAG_BY_NAME)}")
        flags |= flag
    return flags


class RegexMatchScorer:
    """Match ``prediction`` against a per-row regex in ``expected``."""

    name = "regex_match"

    def __init__(self, *, flags: list[str] | None = None, fullmatch: bool = False) -> None:
        self._flags = _resolve_flags(flags or [])
        self._fullmatch = fullmatch

    async def score(
        self,
        prediction: str,
        expected: str,
        *,
        context: ScorerContext | None = None,  # noqa: ARG002
    ) -> ScoreResult:
        try:
            pattern = re.compile(expected, self._flags)
        except re.error as exc:
            raise ConfigError(f"invalid regex pattern {expected!r}: {exc}") from exc
        match = pattern.fullmatch(prediction) if self._fullmatch else pattern.search(prediction)
        is_match = match is not None
        return ScoreResult(
            score=1.0 if is_match else 0.0,
            passed=is_match,
            metadata={
                "scorer": self.name,
                "pattern": expected,
                "fullmatch": self._fullmatch,
                "matched_span": list(match.span()) if match is not None else None,
            },
        )


def _regex_match_factory(options: dict[str, Any]) -> Scorer:
    raw_flags = options.get("flags", [])
    flags = [str(f) for f in raw_flags] if isinstance(raw_flags, (list, tuple)) else []
    return RegexMatchScorer(flags=flags, fullmatch=bool(options.get("fullmatch", False)))


scorer_registry.register("regex_match", _regex_match_factory)
