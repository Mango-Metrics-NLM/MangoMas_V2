"""Tier-1 flow I7: a **streamed** turn is tenant-scoped (specs 0007 + 0025).

Deliberately narrow. ``tests/test_tenancy.py::test_tenancy_middleware_isolates_history``
already covers the invoke path: tenant A invokes, and B's ``/history`` does not
show it. Repeating that here would add a second place to maintain and prove
nothing new.

The gap is the intersection of the two specs. Spec-0025 made streamed turns
persist; spec-0007 scopes persisted turns by tenant. Whether a *streamed* turn
lands under the right tenant depends on the tenant ``ContextVar`` still being
set when the stream finishes draining — which happens inside a
``StreamingResponse`` generator, after the request handler has returned. That
is exactly the shape where a ContextVar is most likely to have been reset, and
nothing tested it.
"""

from __future__ import annotations

import json

import pytest

from tests.constants import STUB_REPLY, TENANCY_ENABLED_ENV
from tests.fakes import FakeLLM
from tests.integration.conftest import (
    ComposedApp,
    ComposeFn,
    read_history,
    turn_prompt,
)

pytestmark = pytest.mark.integration

_STREAM_ROUTE = "/agents/chat/stream"
_TENANT_HEADER = "X-Tenant-ID"
_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_PROMPT_A = "a question only tenant A asked"
_PROMPT_B = "a question only tenant B asked"


async def _stream_as(composed: ComposedApp, tenant: str, content: str) -> None:
    """Drain a stream fully under *tenant* — full drain is what persists."""
    async with (
        composed.client() as client,
        client.stream(
            "POST",
            _STREAM_ROUTE,
            json={"messages": [{"role": "user", "content": content}]},
            headers={_TENANT_HEADER: tenant},
        ) as response,
    ):
        assert response.status_code == 200, response.reason_phrase
        async for line in response.aiter_lines():
            # Drain to the sentinel; persistence happens on full drain only.
            if line.startswith("data: ") and json.loads(line[6:]).get("event") == "done":
                break


async def test_streamed_turns_are_isolated_per_tenant(compose_app: ComposeFn) -> None:
    """Each tenant sees its own streamed turn and nothing of the other's.

    Mutation proof: dropping the ``WHERE tenant = ?`` filter in
    ``SQLiteRepository.list_turns`` makes each tenant see both turns.

    Two tenants rather than one-and-an-empty-check: an isolation bug that
    returned *nothing* would satisfy "B cannot see A's turn" while being
    completely broken.
    """
    composed = compose_app(
        {TENANCY_ENABLED_ENV: "true"},
        llm=FakeLLM(reply=STUB_REPLY),
    )

    await _stream_as(composed, _TENANT_A, _PROMPT_A)
    await _stream_as(composed, _TENANT_B, _PROMPT_B)

    a_turns = await read_history(composed, headers={_TENANT_HEADER: _TENANT_A})
    b_turns = await read_history(composed, headers={_TENANT_HEADER: _TENANT_B})

    assert len(a_turns) == 1, f"tenant A should see exactly its own turn: {a_turns}"
    assert len(b_turns) == 1, f"tenant B should see exactly its own turn: {b_turns}"
    # Identified by the prompt each tenant actually sent: it proves the right
    # row landed under the right tenant, not merely that one row did.
    assert turn_prompt(a_turns[0]) == _PROMPT_A
    assert turn_prompt(b_turns[0]) == _PROMPT_B


async def test_streamed_turns_share_storage_when_tenancy_is_off(
    compose_app: ComposeFn,
) -> None:
    """Default-off: the header is inert and every turn lands in one bucket.

    The other direction of the gate. Without it, a build that scoped storage
    unconditionally would pass the isolation test above while silently
    changing behaviour for every deployment that never enabled tenancy.
    """
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY))

    await _stream_as(composed, _TENANT_A, _PROMPT_A)
    await _stream_as(composed, _TENANT_B, _PROMPT_B)

    assert len(await read_history(composed, headers={_TENANT_HEADER: _TENANT_A})) == 2
