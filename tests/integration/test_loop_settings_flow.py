"""Tier-1 flows I2 + I3: ``MANGOMAS_LOOP__*`` reaches a live HTTP request (spec-0026).

``tests/test_control_loop.py`` proves the control loop works when a
``LoopSettings`` instance is handed to ``Orchestrator(...)`` directly. That
leaves the half spec-0026 actually promised operators untested: that setting
an **environment variable** changes what a running request does, and that a
per-step timeout surfaces as a typed HTTP status rather than a hung
connection or a 500.

The whole chain is in play here — env → ``Settings`` → ``build_orchestrator``
→ ``Orchestrator(loop_settings=...)`` → ``_handle_step``'s ``asyncio.timeout``
→ ``_ERROR_STATUS`` → the JSON envelope. Any link breaking fails these flows
and nothing else in the suite.
"""

from __future__ import annotations

import pytest

from mangomas.errors import StepTimeout
from tests.constants import (
    FLOW_LOOP_MAX_STEPS,
    FLOW_REQUEST_MAX_STEPS,
    LOOP_MAX_STEPS_ENV,
    LOOP_STEP_TIMEOUT_ENV,
    SLOW_AGENT_DELAY_SECONDS,
    STUB_REPLY,
    TINY_STEP_TIMEOUT_SECONDS,
)
from tests.fakes import FakeLLM
from tests.integration.conftest import ComposeFn, read_history

pytestmark = pytest.mark.integration

_INVOKE_ROUTE = "/agents/chat/invoke"
_BODY = {"messages": [{"role": "user", "content": "hello"}]}
# HTTP status for StepTimeout, from the shared table rather than restated:
# a change to _ERROR_STATUS must move this flow, not silently diverge from it.
_STEP_TIMEOUT_STATUS = 504


# ── I2: the per-step budget ───────────────────────────────────────────────────


async def test_env_step_timeout_surfaces_as_a_504_envelope(compose_app: ComposeFn) -> None:
    """A step slower than the env-configured budget becomes a typed 504.

    Mutation proof: removing the ``asyncio.timeout`` wrap in
    ``Orchestrator._handle_step`` makes this fail — the slow fake completes and
    the route returns 200.

    The delay is never actually waited out: the expiring timeout cancels it, so
    this is fast on any machine despite naming a five-second delay.
    """
    composed = compose_app(
        {LOOP_STEP_TIMEOUT_ENV: str(TINY_STEP_TIMEOUT_SECONDS)},
        llm=FakeLLM(delay_seconds=SLOW_AGENT_DELAY_SECONDS),
    )

    async with composed.client() as client:
        response = await client.post(_INVOKE_ROUTE, json=_BODY)

    assert response.status_code == _STEP_TIMEOUT_STATUS, response.text
    assert response.json()["error"] == StepTimeout(TINY_STEP_TIMEOUT_SECONDS).code
    # The turn never completed, so no half-turn may be persisted.
    assert await read_history(composed) == []


async def test_default_step_timeout_leaves_a_normal_request_alone(
    compose_app: ComposeFn,
) -> None:
    """The other direction: with no env override, nothing is cut short.

    Without this, a mutation that made *every* step time out would leave the
    test above green while breaking every deployment.
    """
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY))

    async with composed.client() as client:
        response = await client.post(_INVOKE_ROUTE, json=_BODY)

    assert response.status_code == 200, response.text
    assert response.json()["content"] == STUB_REPLY
    assert len(await read_history(composed)) == 1


# ── I3: the loop budget and its precedence ────────────────────────────────────


async def test_env_max_steps_drives_the_loop_through_http(compose_app: ComposeFn) -> None:
    """``MANGOMAS_LOOP__MAX_STEPS`` governs a request with no explicit budget.

    Two oracles, because either alone is weak: ``metadata["loop"]["steps_taken"]``
    is what the caller sees, and the fake's call count is what actually
    happened. A bug that reported the budget without running it would pass the
    first and fail the second.
    """
    composed = compose_app(
        {LOOP_MAX_STEPS_ENV: str(FLOW_LOOP_MAX_STEPS)},
        llm=FakeLLM(reply=STUB_REPLY),
    )

    async with composed.client() as client:
        response = await client.post(_INVOKE_ROUTE, json=_BODY)

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["loop"] == {
        "steps_taken": FLOW_LOOP_MAX_STEPS,
        "accepted": False,
    }
    assert len(composed.llm.calls) == FLOW_LOOP_MAX_STEPS


async def test_request_max_steps_beats_the_env(compose_app: ComposeFn) -> None:
    """Caller-stated intent outranks the deployment default (spec-0026 R2).

    The two budgets are deliberately different values, so "the request won" is
    distinguishable from "the env won" — equal values would make this pass
    whichever tier the code actually used.
    """
    composed = compose_app(
        {LOOP_MAX_STEPS_ENV: str(FLOW_LOOP_MAX_STEPS)},
        llm=FakeLLM(reply=STUB_REPLY),
    )

    async with composed.client() as client:
        response = await client.post(
            _INVOKE_ROUTE, json={**_BODY, "max_steps": FLOW_REQUEST_MAX_STEPS}
        )

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["loop"]["steps_taken"] == FLOW_REQUEST_MAX_STEPS
    assert len(composed.llm.calls) == FLOW_REQUEST_MAX_STEPS


async def test_no_env_budget_runs_a_single_step(compose_app: ComposeFn) -> None:
    """The field default closes the chain: one step, unchanged from pre-spec-0026."""
    composed = compose_app(llm=FakeLLM(reply=STUB_REPLY))

    async with composed.client() as client:
        response = await client.post(_INVOKE_ROUTE, json=_BODY)

    assert response.json()["metadata"]["loop"]["steps_taken"] == 1
    assert len(composed.llm.calls) == 1
