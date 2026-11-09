"""Per-task accumulation of LLM token usage (spec-0045 B3).

The :class:`~mangomas.adapters.llm.base.LLMClient` protocol returns a ``str``;
adding a usage return would break every implementer, and a ``last_usage``
attribute on a client is racy the moment two rows share it. Instead an adapter
*reports* each completion into whatever :class:`UsageTotals` the current task
has open, and a caller that wants the figure opens one with
:func:`usage_scope` around the work it means to measure.

The scope is a :class:`contextvars.ContextVar` holding a **mutable** object, so
the child tasks ``asyncio.gather`` spawns inside the scope (a fan-out's
branches) copy the context, see the same object, and add to it. With no scope
open every report is a no-op, which is why an adapter can call
:func:`record_call` unconditionally.

``llm_calls`` is counted even when the provider sent no usage block, so a
reader can tell "three calls, zero tokens reported" (a silent provider) from
"no call was made" (a deterministic target). Nothing here imports a layer above
``utils``: key names for the exported mapping are supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

# OpenAI-compatible usage field names, as LM Studio sends them.
_PROMPT_TOKENS_FIELD = "prompt_tokens"
_COMPLETION_TOKENS_FIELD = "completion_tokens"


@dataclass
class UsageTotals:
    """Running totals for one measured scope."""

    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0

    def as_metadata(self, *, input_key: str, output_key: str, calls_key: str) -> dict[str, int]:
        """Export the totals under the caller's key names."""
        return {
            input_key: self.input_tokens,
            output_key: self.output_tokens,
            calls_key: self.llm_calls,
        }


_usage_var: ContextVar[UsageTotals | None] = ContextVar("mangomas_llm_usage", default=None)


@contextmanager
def usage_scope() -> Iterator[UsageTotals]:
    """Open a measured scope; every :func:`record_call` inside adds to the yielded totals.

    Scopes nest: an inner scope sees only its own calls and the outer scope is
    restored on exit. A caller measuring a dispatch must therefore close the
    scope *before* doing further LLM work (a judge scorer, say) it does not
    want billed to the same row.
    """
    totals = UsageTotals()
    token = _usage_var.set(totals)
    try:
        yield totals
    finally:
        _usage_var.reset(token)


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def record_call(usage: Mapping[str, object] | None) -> None:
    """Report one completion to the open scope, if any.

    *usage* is the provider's usage block (``prompt_tokens`` /
    ``completion_tokens``), or ``None`` when the provider sent none; the call is
    counted either way. Malformed counts (negative, boolean, non-integer) read
    as zero rather than raising — a provider's accounting must never fail a
    completion that succeeded.
    """
    totals = _usage_var.get()
    if totals is None:
        return
    totals.llm_calls += 1
    if usage is None:
        return
    totals.input_tokens += _non_negative_int(usage.get(_PROMPT_TOKENS_FIELD))
    totals.output_tokens += _non_negative_int(usage.get(_COMPLETION_TOKENS_FIELD))
