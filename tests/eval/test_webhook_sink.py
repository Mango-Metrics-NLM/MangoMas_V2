"""Tests for the webhook eval sink (httpx mocked with respx — no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

import mangomas.eval.sinks  # noqa: F401 — registers built-in sinks
from mangomas.errors import ConfigError, PersistenceError
from mangomas.eval import evaluate_gate
from mangomas.eval.runner import EvalReport
from mangomas.eval.sink_registry import sink_registry
from mangomas.eval.sinks.webhook import WebhookSink

_URL = "https://hooks.example.test/eval"


def _report() -> EvalReport:
    return EvalReport(
        scorer="exact_match",
        agent_name="chat",
        dataset_size=1,
        passed=1,
        failed=0,
        errored=0,
        mean_score=1.0,
        duration_ms=1.0,
        rows=[],
        target_name="chat",
    )


@respx.mock
async def test_webhook_sink_posts_report() -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(200))
    await WebhookSink(url=_URL).emit(
        _report(), gate_result=evaluate_gate(_report(), min_mean_score=0.5)
    )
    assert route.called
    payload = json.loads(route.calls.last.request.content)
    assert payload["scorer"] == "exact_match"
    assert payload["target_name"] == "chat"
    assert payload["gate"]["passed"] is True


@respx.mock
async def test_webhook_sink_raises_typed_error_without_the_url_on_non_2xx() -> None:
    """A non-2xx is a typed ``PersistenceError`` whose message carries the origin
    only: Slack/Teams webhook URLs embed their secret in the path, and
    ``str(HTTPStatusError)`` would have echoed it through the CLI."""
    respx.post(_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(PersistenceError) as excinfo:
        await WebhookSink(url=_URL).emit(_report())
    message = str(excinfo.value)
    assert "HTTP 500" in message
    assert _URL not in message
    assert excinfo.value.detail == "boom"


@respx.mock
async def test_webhook_sink_wraps_transport_errors() -> None:
    respx.post(_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(PersistenceError) as excinfo:
        await WebhookSink(url=_URL).emit(_report())
    assert _URL not in str(excinfo.value)


@pytest.mark.parametrize("bad", [-1, float("nan"), "soon", True])
def test_webhook_factory_rejects_bad_timeout(bad: object) -> None:
    with pytest.raises(ConfigError, match="timeout_seconds"):
        sink_registry.get("webhook")({"url": _URL, "timeout_seconds": bad})


def test_webhook_factory_requires_url() -> None:
    with pytest.raises(ConfigError):
        sink_registry.get("webhook")({})


def test_webhook_factory_reads_timeout() -> None:
    sink = sink_registry.get("webhook")({"url": _URL, "timeout_seconds": 2.5})
    assert isinstance(sink, WebhookSink)


def test_webhook_in_registry() -> None:
    assert "webhook" in sink_registry.available()
