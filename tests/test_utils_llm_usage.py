"""The per-task usage accumulator (spec-0045 B3): scoped, shared across gathered tasks, tolerant."""

from __future__ import annotations

import asyncio

from mangomas.utils.llm_usage import UsageTotals, record_call, usage_scope

_USAGE = {"prompt_tokens": 12, "completion_tokens": 7}


def test_no_open_scope_makes_record_call_a_no_op() -> None:
    record_call(_USAGE)  # must not raise, must not leak into a later scope
    with usage_scope() as totals:
        pass
    assert totals == UsageTotals()


def test_scope_accumulates_tokens_and_counts_every_call() -> None:
    with usage_scope() as totals:
        record_call(_USAGE)
        record_call(_USAGE)
        record_call(None)  # a provider that sent no usage block still made a call
    assert totals == UsageTotals(input_tokens=24, output_tokens=14, llm_calls=3)


def test_scopes_nest_and_the_outer_is_restored() -> None:
    with usage_scope() as outer:
        record_call(_USAGE)
        with usage_scope() as inner:
            record_call(_USAGE)
        record_call(_USAGE)
    assert inner.llm_calls == 1
    assert outer.llm_calls == 2, "the inner scope's call is not the outer's"


async def test_gathered_tasks_report_into_the_shared_scope() -> None:
    async def one_call() -> None:
        await asyncio.sleep(0)
        record_call(_USAGE)

    with usage_scope() as totals:
        await asyncio.gather(one_call(), one_call(), one_call())
    assert totals.llm_calls == 3
    assert totals.input_tokens == 36


def test_malformed_counts_read_as_zero_not_as_an_error() -> None:
    with usage_scope() as totals:
        record_call({"prompt_tokens": -5, "completion_tokens": True})
        record_call({"prompt_tokens": "12", "completion_tokens": 3.5})
        record_call({})
    assert totals == UsageTotals(input_tokens=0, output_tokens=0, llm_calls=3)


def test_as_metadata_uses_the_callers_key_names() -> None:
    totals = UsageTotals(input_tokens=1, output_tokens=2, llm_calls=3)
    assert totals.as_metadata(input_key="in", output_key="out", calls_key="n") == {
        "in": 1,
        "out": 2,
        "n": 3,
    }
