"""Tests for the optional Langfuse eval sink.

Two layers:

* **Ungated** unit tests cover the lazy-import guard (missing SDK → ``ConfigError``)
  and the full publish path using a *fake* ``langfuse`` module injected into
  ``sys.modules`` — so the production code runs without the real extra installed.
* A **gated** smoke test (``@pytest.mark.langfuse``, ``RUN_LANGFUSE=1``) exercises
  the real SDK + live credentials and is skipped by default.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

# Importing this package registers the sink factory.
import mangomas.eval.sinks  # noqa: F401
from mangomas.errors import ConfigError
from mangomas.eval import evaluate_gate
from mangomas.eval.runner import EvalReport
from mangomas.eval.sinks.langfuse import LangfuseSink, _langfuse_factory


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
    )


class _FakeLangfuseClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.traces: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self.flushed = 0

    def trace(self, **kwargs: Any) -> Any:
        self.traces.append(kwargs)
        return types.SimpleNamespace(id="trace-1")

    def score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flushed += 1


def _install_fake_langfuse(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, _FakeLangfuseClient]:
    captured: dict[str, _FakeLangfuseClient] = {}

    def _factory(**kwargs: Any) -> _FakeLangfuseClient:
        client = _FakeLangfuseClient(**kwargs)
        captured["client"] = client
        return client

    module = types.ModuleType("langfuse")
    module.Langfuse = _factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", module)
    return captured


def test_langfuse_sink_missing_sdk_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Setting the module to None makes ``import langfuse`` raise ImportError.
    monkeypatch.setitem(sys.modules, "langfuse", None)
    with pytest.raises(ConfigError):
        _langfuse_factory({})


def test_langfuse_sink_bad_options_raise_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that fails to construct surfaces as ConfigError at init."""

    def _boom(**_: Any) -> Any:
        raise ValueError("bad credentials")

    module = types.ModuleType("langfuse")
    module.Langfuse = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", module)
    with pytest.raises(ConfigError):
        _langfuse_factory({"public_key": "x"})


async def test_langfuse_sink_publishes_with_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_langfuse(monkeypatch)
    sink = LangfuseSink(options={"public_key": "pk"})
    await sink.emit(_report())
    client = captured["client"]
    assert client.kwargs == {"public_key": "pk"}
    assert client.traces and client.traces[0]["name"] == "mangomas-eval"
    assert client.scores and client.scores[0]["value"] == 1.0
    assert client.flushed == 1


async def test_langfuse_sink_includes_gate_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_langfuse(monkeypatch)
    gate = evaluate_gate(_report(), min_mean_score=0.5)
    await LangfuseSink().emit(_report(), gate_result=gate)
    metadata = captured["client"].traces[0]["metadata"]
    assert metadata["gate_passed"] is True
    assert "gate_reasons" in metadata


async def test_langfuse_sink_flushes_even_on_publish_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_langfuse(monkeypatch)
    sink = LangfuseSink()

    def _raise(self: _FakeLangfuseClient, **_: Any) -> None:  # noqa: ARG001
        raise RuntimeError("boom")

    # Make score() raise to prove flush() still runs (finally block).
    monkeypatch.setattr(_FakeLangfuseClient, "score", _raise)
    with pytest.raises(RuntimeError):
        await sink.emit(_report())
    assert captured["client"].flushed == 1


@pytest.mark.langfuse
async def test_langfuse_sink_real_smoke() -> None:  # pragma: no cover — gated
    """Real SDK smoke test; requires RUN_LANGFUSE=1 + live credentials."""
    sink = LangfuseSink()
    await sink.emit(_report())
