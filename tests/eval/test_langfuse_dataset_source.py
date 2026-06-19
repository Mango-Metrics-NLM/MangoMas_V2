"""Tests for the optional Langfuse dataset source.

Ungated unit tests cover the lazy-import guard and the item→``DatasetRow``
mapping using a *fake* ``langfuse`` module injected into ``sys.modules`` (so the
code runs without the real extra). A gated smoke test (``@pytest.mark.langfuse``,
``RUN_LANGFUSE=1``) exercises the real SDK and is skipped by default.
"""

from __future__ import annotations

import os
import sys
import types
from typing import Any

import pytest

import mangomas.eval.sources  # noqa: F401 — registers the source factory
from mangomas.errors import ConfigError
from mangomas.eval.dataset_source import dataset_source_registry
from mangomas.eval.sources.langfuse import LangfuseDatasetSource


class _FakeItem:
    def __init__(self, input_value: Any, expected: Any, item_id: str | None = None) -> None:
        self.input = input_value
        self.expected_output = expected
        self.id = item_id


class _FakeDataset:
    def __init__(self, items: list[_FakeItem]) -> None:
        self.items = items


class _FakeLangfuseClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def get_dataset(self, name: str) -> _FakeDataset:  # noqa: ARG002
        return _FakeDataset(
            [
                _FakeItem("hello", "world", item_id="a"),
                _FakeItem({"messages": [{"role": "user", "content": "hi"}]}, "ok"),
            ]
        )


def _install_fake_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("langfuse")
    module.Langfuse = _FakeLangfuseClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", module)


def test_langfuse_source_missing_sdk_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "langfuse", None)
    with pytest.raises(ConfigError):
        dataset_source_registry.get("langfuse")({"dataset": "d"})


def test_langfuse_source_requires_dataset_option(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langfuse(monkeypatch)
    with pytest.raises(ConfigError):
        dataset_source_registry.get("langfuse")({})


async def test_langfuse_source_maps_items(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langfuse(monkeypatch)
    source = dataset_source_registry.get("langfuse")({"dataset": "d", "public_key": "pk"})
    rows = await source.load()
    assert len(rows) == 2
    assert rows[0].id == "a"
    assert rows[0].messages[0].content == "hello"
    assert rows[0].expected == "world"
    # A dict-with-messages input is passed straight through.
    assert rows[1].messages[0].content == "hi"
    assert rows[1].expected == "ok"


def test_langfuse_source_filters_dataset_from_client_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_langfuse(monkeypatch)
    source = LangfuseDatasetSource(dataset="d", options={"dataset": "d", "public_key": "pk"})
    assert source._client.kwargs == {"public_key": "pk"}


@pytest.mark.langfuse
async def test_langfuse_source_real_smoke() -> None:  # pragma: no cover — gated
    """Real SDK smoke; requires RUN_LANGFUSE=1, live creds, and LANGFUSE_DATASET."""
    name = os.environ.get("LANGFUSE_DATASET", "mangomas-smoke")
    rows = await LangfuseDatasetSource(dataset=name).load()
    assert isinstance(rows, list)
