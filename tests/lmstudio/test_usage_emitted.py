"""LM Studio really sends a usage block (spec-0045 B3). Skipped unless ``RUN_LMSTUDIO=1``.

The respx suite proves the adapter *reads* ``usage``; only a live server can
prove the server *writes* it. Without this, "measured cost" would rest on an
assumption about a provider nobody in CI ever talks to.
"""

from __future__ import annotations

import pytest

from mangomas.core import Message, Orchestrator
from mangomas.utils.llm_usage import usage_scope


@pytest.mark.lmstudio
async def test_live_completion_reports_token_usage(lmstudio_orchestrator: Orchestrator) -> None:
    llm = lmstudio_orchestrator.context.llm
    with usage_scope() as totals:
        content = await llm.complete([Message(role="user", content="Reply with one word.")])
    assert content
    assert totals.llm_calls == 1
    assert totals.input_tokens > 0, (
        "LM Studio returned no prompt_tokens — measured cost is unfounded"
    )
    assert totals.output_tokens > 0
