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
from mangomas.eval._options import require_bool
from mangomas.eval.protocol import Scorer, ScorerContext, ScoreResult
from mangomas.eval.registry import scorer_registry
from mangomas.utils.regex_flags import resolve_regex_flags


class RegexMatchScorer:
    """Match ``prediction`` against a per-row regex in ``expected``."""

    name = "regex_match"

    def __init__(self, *, flags: list[str] | None = None, fullmatch: bool = False) -> None:
        self._flags = resolve_regex_flags(flags or [])
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
    if not isinstance(raw_flags, list | tuple):
        # Factory-time rule: a mistyped option (``"flags": "ignorecase"``) is a
        # config error, not silently no flags.
        raise ConfigError("regex_match: 'flags' option must be a list of flag names")
    flags = [str(f) for f in raw_flags]
    fullmatch = require_bool(options, "fullmatch", default=False, owner="regex_match")
    return RegexMatchScorer(flags=flags, fullmatch=fullmatch)


scorer_registry.register("regex_match", _regex_match_factory)
