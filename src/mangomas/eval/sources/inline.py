"""Inline dataset source — rows supplied directly via options.

Useful for tests and small, config-embedded datasets. Each raw row is validated
through the same :func:`~mangomas.eval.dataset._parse_row` core the JSONL loader
uses, so an inline dataset enforces an identical schema.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from mangomas.eval._options import require_list
from mangomas.eval.dataset import _parse_row
from mangomas.eval.dataset_source import DatasetSource, dataset_source_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.dataset import DatasetRow


class InlineSource:
    """Validate and return a list of raw row dicts passed at construction."""

    name = "inline"

    def __init__(self, *, rows: list[Any]) -> None:
        self._rows = rows

    async def load(self) -> list[DatasetRow]:
        return await asyncio.to_thread(self._parse)

    def _parse(self) -> list[DatasetRow]:
        return [
            _parse_row(raw, index=index, source="inline")
            for index, raw in enumerate(self._rows, start=1)
        ]


def _inline_source_factory(options: dict[str, Any]) -> DatasetSource:
    rows = require_list(options, "rows", owner="inline dataset source")
    return InlineSource(rows=list(rows))


dataset_source_registry.register("inline", _inline_source_factory)
