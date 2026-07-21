"""DatasetSource protocol — where an eval dataset is loaded from.

A :class:`DatasetSource` decouples the harness from a single on-disk JSONL file:
rows can come from a local file (``jsonl``), be passed inline (``inline``), or be
fetched from an external store (``langfuse``). Sources are resolved by name
through :data:`dataset_source_registry`, exactly as scorers / sinks / targets
are, and every source yields the same validated :class:`DatasetRow` list.

``load`` is async because real sources perform I/O (file reads, SDK calls) which
must run off the event loop via ``asyncio.to_thread`` per project convention.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from mangomas.registry import Registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.dataset import DatasetRow


@runtime_checkable
class DatasetSource(Protocol):
    """A named producer of validated evaluation rows."""

    name: str

    async def load(self) -> list[DatasetRow]:
        """Load and validate every row from this source."""
        ...


DatasetSourceFactory = Callable[[dict[str, Any]], DatasetSource]
dataset_source_registry: Registry[DatasetSourceFactory] = Registry("dataset_source")
