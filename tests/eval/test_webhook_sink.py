"""Tests for the webhook eval sink (httpx mocked with respx — no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

import mangomas.eval.sinks  # noqa: F401 — registers built-in sinks
from mangomas.errors import ConfigError
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
async def test_webhook_sink_raises_on_non_2xx() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await WebhookSink(url=_URL).emit(_report())


def test_webhook_factory_requires_url() -> None:
    with pytest.raises(ConfigError):
        sink_registry.get("webhook")({})


def test_webhook_factory_reads_timeout() -> None:
    sink = sink_registry.get("webhook")({"url": _URL, "timeout_seconds": 2.5})
    assert isinstance(sink, WebhookSink)


def test_webhook_in_registry() -> None:
    assert "webhook" in sink_registry.available()
