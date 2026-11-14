"""Regression + AQA guards for ``eval_harness_bridge`` (spec-0050 D-5)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

import convert_dataset
import mango_bridge
from tests.eval_harness_bridge.constants import (
    BRIDGE_BASE_URL,
    DEFAULT_AGENT,
    HTTP_OK,
    invoke_url,
    mango_response,
)

_BASE_ROW: dict[str, Any] = {
    "id": "r1",
    "messages": [{"role": "user", "content": "hi"}],
    "expected": "hello",
}


@pytest.fixture(autouse=True)
def _reset_bridge_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MANGO_BASE_URL", BRIDGE_BASE_URL)
    monkeypatch.setenv("MANGO_AGENT", DEFAULT_AGENT)
    mango_bridge.close_client()
    yield
    mango_bridge.close_client()


@pytest.mark.parametrize("falsy_non_dict", [[], "", 0, False])
def test_d5_bridge_rejects_falsy_non_dict_metadata(falsy_non_dict: Any) -> None:
    """Falsy non-dicts (``[]``, ``""``, ``0``, ``False``) raise instead of coercing to ``{}``."""
    with pytest.raises(ValueError, match="'metadata' must be an object"):
        convert_dataset.convert_row({**_BASE_ROW, "metadata": falsy_non_dict})
    with pytest.raises(ValueError, match=r"inputs\.metadata must be an object"):
        mango_bridge.predict({"question": "hi", "metadata": falsy_non_dict})


@pytest.mark.parametrize(
    "bad_agent",
    ["", "   ", "../admin", "chat/../../etc", "chat?x=1", "chat#frag", "bad agent"],
)
def test_d5_mango_bridge_rejects_unsafe_agent_slug(bad_agent: str) -> None:
    """Agent names interpolated into ``/agents/{agent}/invoke`` must match a strict slug."""
    with pytest.raises(ValueError, match="invalid agent"):
        mango_bridge.predict({"question": "hi", "agent": bad_agent})


@pytest.mark.parametrize("bad_timeout", ["0", "-5", "nan", "inf", "not-a-float"])
def test_d5_mango_bridge_rejects_invalid_timeout(
    bad_timeout: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MANGO_TIMEOUT`` must be a finite positive float."""
    monkeypatch.setenv("MANGO_TIMEOUT", bad_timeout)
    with pytest.raises(ValueError, match="MANGO_TIMEOUT"):
        mango_bridge._client()


@respx.mock
def test_d5_mango_bridge_accepts_valid_slug_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive boundary: valid agent slug and finite positive timeout succeed."""
    monkeypatch.setenv("MANGO_TIMEOUT", "15.5")
    route = respx.post(invoke_url("chat_v1.2-ok")).mock(
        return_value=httpx.Response(HTTP_OK, json=mango_response("ok"))
    )
    assert mango_bridge.predict({"question": "hi", "agent": "chat_v1.2-ok"}) == "ok"
    assert route.called


@pytest.mark.parametrize("bad_steps", [True, False, None, "abc", "1.5", 1.5, 0, "0", -2])
def test_bridge_rejects_invalid_max_steps_in_inputs_and_env(
    bad_steps: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="max_steps"):
        mango_bridge.predict({"question": "hi", "max_steps": bad_steps})
    if isinstance(bad_steps, str):
        monkeypatch.setenv("MANGO_MAX_STEPS", bad_steps)
        with pytest.raises(ValueError, match="MANGO_MAX_STEPS"):
            mango_bridge.predict({"question": "hi"})


@respx.mock
def test_bridge_accepts_positive_max_steps_from_inputs_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.post(invoke_url(DEFAULT_AGENT)).mock(
        return_value=httpx.Response(HTTP_OK, json=mango_response("ok"))
    )
    assert mango_bridge.predict({"question": "hi", "max_steps": 3}) == "ok"
    assert mango_bridge.predict({"question": "hi", "max_steps": " 2 "}) == "ok"
    monkeypatch.setenv("MANGO_MAX_STEPS", "4")
    assert mango_bridge.predict({"question": "hi"}) == "ok"
    assert route.call_count == 3
