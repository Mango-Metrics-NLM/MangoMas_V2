"""JSONL dataset source — the default, file-backed source.

A thin wrapper over :func:`~mangomas.eval.dataset.load_jsonl` so the existing
loader (and its validation) stays the single source of truth; this source only
adds registry resolution and the ``path`` option.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mangomas.eval._options import require_str
from mangomas.eval.dataset import load_jsonl
from mangomas.eval.dataset_source import DatasetSource, dataset_source_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.dataset import DatasetRow


class JsonlSource:
    """Load rows from a JSONL file."""

    name = "jsonl"

    def __init__(self, *, path: str) -> None:
        self._path = path

    async def load(self) -> list[DatasetRow]:
        return await load_jsonl(self._path)


def _jsonl_source_factory(options: dict[str, Any]) -> DatasetSource:
    path = require_str(options, "path", owner="jsonl dataset source")
    return JsonlSource(path=path)


dataset_source_registry.register("jsonl", _jsonl_source_factory)
