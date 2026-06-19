"""Inline dataset source — rows supplied directly via options.

Useful for tests and small, config-embedded datasets. Each raw row is validated
through the same :func:`~mangomas.eval.dataset._parse_row` core the JSONL loader
uses, so an inline dataset enforces an identical schema.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from mangomas.errors import ConfigError
from mangomas.eval.dataset import _parse_row
from mangomas.eval.dataset_source import DatasetSource, dataset_source_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.dataset import DatasetRow

logger = logging.getLogger(__name__)


class InlineSource:
    """Validate and return a list of raw row dicts passed at construction."""

    name = "inline"

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def load(self) -> list[DatasetRow]:
        return await asyncio.to_thread(self._parse)

    def _parse(self) -> list[DatasetRow]:
        return [
            _parse_row(raw, index=index, source="inline")
            for index, raw in enumerate(self._rows, start=1)
        ]


def _inline_source_factory(options: dict[str, Any]) -> DatasetSource:
    rows = options.get("rows")
    if not isinstance(rows, list):
        raise ConfigError("inline dataset source requires a 'rows' list")
    return InlineSource(list(rows))


dataset_source_registry.register("inline", _inline_source_factory)
