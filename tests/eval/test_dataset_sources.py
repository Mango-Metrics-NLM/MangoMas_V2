"""Tests for the built-in dataset sources and the dataset-source registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.errors import ConfigError
from mangomas.eval import dataset_source_registry, load_jsonl
from mangomas.eval.dataset import DatasetError
from mangomas.eval.dataset_source import DatasetSource
from mangomas.eval.sources import InlineSource, JsonlSource

_INLINE_ROW = {
    "id": "i1",
    "messages": [{"role": "user", "content": "hi"}],
    "expected": "ok",
}


# ── jsonl source ──────────────────────────────────────────────────────────────


async def test_jsonl_source_matches_load_jsonl(fixtures_dir: Path) -> None:
    path = fixtures_dir / "mixed.jsonl"
    via_source = await JsonlSource(str(path)).load()
    via_loader = await load_jsonl(path)
    assert [r.id for r in via_source] == [r.id for r in via_loader]
    assert len(via_source) == 3


def test_jsonl_factory_requires_path() -> None:
    with pytest.raises(ConfigError):
        dataset_source_registry.get("jsonl")({})


# ── inline source ─────────────────────────────────────────────────────────────


async def test_inline_source_loads_and_validates() -> None:
    source = InlineSource([_INLINE_ROW])
    rows = await source.load()
    assert len(rows) == 1
    assert rows[0].id == "i1"
    assert rows[0].expected == "ok"


async def test_inline_source_rejects_bad_row() -> None:
    # Missing 'expected' → the shared _parse_row validator raises DatasetError.
    source = InlineSource([{"messages": [{"role": "user", "content": "x"}]}])
    with pytest.raises(DatasetError):
        await source.load()


def test_inline_factory_requires_rows_list() -> None:
    with pytest.raises(ConfigError):
        dataset_source_registry.get("inline")({})
    with pytest.raises(ConfigError):
        dataset_source_registry.get("inline")({"rows": "not-a-list"})


# ── registry ──────────────────────────────────────────────────────────────────


def test_registry_exposes_builtin_sources() -> None:
    for name in ("inline", "jsonl", "langfuse"):
        assert name in dataset_source_registry.available()


def test_sources_satisfy_protocol() -> None:
    assert isinstance(JsonlSource("x"), DatasetSource)
    assert isinstance(InlineSource([]), DatasetSource)
