"""Regression + AQA guards for ``eval_harness_bridge`` (spec-0050 D-5)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import MappingProxyType
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


def test_to_messages_treats_none_user_field_as_absent_and_falls_through() -> None:
    """PR #20 e85dd1d gap: explicit None falls through instead of coercing to 'None'."""
    assert mango_bridge.to_messages({"question": None, "prompt": "P"}) == [
        {"role": "user", "content": "P"}
    ]
    assert mango_bridge.to_messages({"question": None}) == [{"role": "user", "content": ""}]


@pytest.mark.parametrize("field", ["question", "prompt", "input"])
@pytest.mark.parametrize("bad_user", [123, ["list"], {"dict": 1}, False, True])
def test_to_messages_rejects_non_string_user_field(
    field: str,
    bad_user: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger="mango_bridge"),
        pytest.raises(ValueError, match=rf"inputs\.{field} must be a string"),
    ):
        mango_bridge.to_messages({field: bad_user})
    assert any(
        getattr(rec, "event", None) == "bridge_invalid_field"
        and getattr(rec, "field", None) == f"inputs.{field}"
        and getattr(rec, "actual_type", None) == type(bad_user).__name__
        for rec in caplog.records
    )


@pytest.mark.parametrize(
    ("payload", "bad_field"),
    [
        ({"question": "hi", "prompt": 123}, "prompt"),
        ({"question": "", "input": False}, "input"),
    ],
)
def test_to_messages_validates_lower_precedence_user_fields(
    payload: dict[str, Any],
    bad_field: str,
) -> None:
    with pytest.raises(ValueError, match=rf"inputs\.{bad_field} must be a string"):
        mango_bridge.to_messages(payload)


def test_to_messages_validates_optional_system_string() -> None:
    assert mango_bridge.to_messages({"system": None, "question": "hi"}) == [
        {"role": "user", "content": "hi"}
    ]
    assert mango_bridge.to_messages({"system": "", "question": "hi"}) == [
        {"role": "user", "content": "hi"}
    ]
    assert mango_bridge.to_messages(
        MappingProxyType({"messages": [MappingProxyType({"role": "user", "content": "hi"})]})
    ) == [{"role": "user", "content": "hi"}]
    with pytest.raises(ValueError, match=r"inputs\.system must be a string"):
        mango_bridge.to_messages({"system": 123, "question": "hi"})


@pytest.mark.parametrize("bad_agent", [True, False, 123, 1.5, ["bad"]])
def test_predict_rejects_non_string_agent(bad_agent: Any) -> None:
    """Non-string agent overrides must fail loud instead of coercing True/int into slugs."""
    with pytest.raises(ValueError, match=r"inputs\.agent must be a string"):
        mango_bridge.predict({"question": "hi", "agent": bad_agent})


@pytest.mark.parametrize("bad_inputs", [None, ["list"], "text", 42])
def test_bridge_rejects_non_mapping_inputs(bad_inputs: Any) -> None:
    """Non-mapping top-level inputs fail loud with ValueError instead of AttributeError."""
    with pytest.raises(ValueError, match="inputs must be a mapping"):
        mango_bridge.to_messages(bad_inputs)
    with pytest.raises(ValueError, match="inputs must be a mapping"):
        mango_bridge.predict(bad_inputs)
