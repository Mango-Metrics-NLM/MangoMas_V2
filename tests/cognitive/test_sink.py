"""JSONL / HTTP / composite cognitive sinks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx
from tests.constants import SIGNAL_MOCK_INGEST_URL
from tests.fakes import FakeCognitiveSink
from tests.mango_contracts.constants import envelope_base

from mango_contracts import CognitiveSignal
from mangomas.cognitive.constants import JSONL_FILENAME
from mangomas.cognitive.sink import (
    CognitiveSignalSink,
    CompositeCognitiveSink,
    HttpCognitiveSink,
    JsonlCognitiveSink,
    build_sink,
)
from mangomas.config import SignalSettings

_URL = SIGNAL_MOCK_INGEST_URL


def _signal() -> CognitiveSignal:
    return CognitiveSignal.model_validate(envelope_base())


async def test_jsonl_sink_appends_one_line(tmp_path: Path) -> None:
    path = tmp_path / "signals.jsonl"
    sink = JsonlCognitiveSink(path)
    await sink.emit(_signal())
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    restored = CognitiveSignal.model_validate_json(lines[0])
    assert restored.producer_id == "mangomas.reviewer.v2"


@respx.mock
async def test_http_sink_posts_envelope() -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(202))
    await HttpCognitiveSink(_URL, timeout_seconds=1.0).emit(_signal())
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["signal_kind"] == "review.finding"
    assert "confidence" in body  # observation envelope may carry it
    assert "allowed_tools" not in body


@respx.mock
async def test_http_sink_raises_on_non_2xx() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await HttpCognitiveSink(_URL, timeout_seconds=1.0).emit(_signal())


@respx.mock
async def test_composite_writes_jsonl_even_when_http_fails(tmp_path: Path) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(500))
    path = tmp_path / "signals.jsonl"
    sink = CompositeCognitiveSink(
        (JsonlCognitiveSink(path), HttpCognitiveSink(_URL, timeout_seconds=1.0))
    )
    with pytest.raises(httpx.HTTPStatusError):
        await sink.emit(_signal())
    assert path.is_file()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_build_sink_jsonl_only(tmp_path: Path) -> None:
    settings = SignalSettings(enabled=True, dir=str(tmp_path), http_url=None)
    sink = build_sink(settings)
    assert isinstance(sink, JsonlCognitiveSink)


async def test_build_sink_jsonl_path_uses_filename_constant(tmp_path: Path) -> None:
    settings = SignalSettings(enabled=True, dir=str(tmp_path), http_url=None)
    sink = build_sink(settings)
    await sink.emit(_signal())
    assert (tmp_path / JSONL_FILENAME).is_file()


def test_build_sink_composes_http(tmp_path: Path) -> None:
    settings = SignalSettings(enabled=True, dir=str(tmp_path), http_url=_URL)
    sink = build_sink(settings)
    assert isinstance(sink, CompositeCognitiveSink)


async def test_composite_continues_after_first_inner_failure() -> None:
    left = FakeCognitiveSink(raise_on_emit=RuntimeError("first"))
    right = FakeCognitiveSink()
    with pytest.raises(RuntimeError, match="first"):
        await CompositeCognitiveSink((left, right)).emit(_signal())
    assert len(right.emitted) == 1


def test_jsonl_and_http_and_composite_satisfy_the_protocol(tmp_path: Path) -> None:
    assert isinstance(JsonlCognitiveSink(tmp_path / JSONL_FILENAME), CognitiveSignalSink)
    assert isinstance(HttpCognitiveSink(_URL, timeout_seconds=1.0), CognitiveSignalSink)
    assert isinstance(
        CompositeCognitiveSink((FakeCognitiveSink(), FakeCognitiveSink())),
        CognitiveSignalSink,
    )


@respx.mock
async def test_http_sink_timeout_raises() -> None:
    respx.post(_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    with pytest.raises(httpx.TimeoutException):
        await HttpCognitiveSink(_URL, timeout_seconds=0.01).emit(_signal())


async def test_jsonl_concurrent_appends(tmp_path: Path) -> None:
    path = tmp_path / JSONL_FILENAME
    sink = JsonlCognitiveSink(path)
    await asyncio.gather(*[sink.emit(_signal()) for _ in range(8)])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8
    for line in lines:
        CognitiveSignal.model_validate_json(line)


async def test_composite_all_success() -> None:
    left = FakeCognitiveSink()
    right = FakeCognitiveSink()
    await CompositeCognitiveSink((left, right)).emit(_signal())
    assert len(left.emitted) == 1
    assert len(right.emitted) == 1


async def test_composite_both_fail_raises_first() -> None:
    left = FakeCognitiveSink(raise_on_emit=RuntimeError("first"))
    right = FakeCognitiveSink(raise_on_emit=RuntimeError("second"))
    with pytest.raises(RuntimeError, match="first"):
        await CompositeCognitiveSink((left, right)).emit(_signal())
