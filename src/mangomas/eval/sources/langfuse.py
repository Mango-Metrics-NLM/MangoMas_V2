"""Langfuse dataset source — fetch a named dataset from Langfuse (optional extra).

Langfuse is an **optional** dependency (``pip install 'mangomas[langfuse]'``); the
SDK is lazy-imported so the package stays usable without it, mirroring the
Langfuse sink and the Vertex / Chroma adapters. Credentials are read from the
environment by the SDK (``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` /
``LANGFUSE_HOST``); nothing is hard-coded. Dataset items are mapped onto the
harness's :class:`DatasetRow` schema via the shared
:func:`~mangomas.eval.dataset._parse_row` validator.
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


def _import_langfuse() -> Any:
    """Import the optional ``langfuse`` SDK or raise a clear ``ConfigError``."""
    try:
        import langfuse  # noqa: PLC0415
    except ImportError as exc:
        raise ConfigError(
            "langfuse dataset source requires the 'langfuse' extra: "
            "pip install 'mangomas[langfuse]'"
        ) from exc
    return langfuse


class LangfuseDatasetSource:
    """Fetch a named Langfuse dataset and map its items to :class:`DatasetRow`."""

    name = "langfuse"

    def __init__(self, *, dataset: str, options: dict[str, Any] | None = None) -> None:
        langfuse = _import_langfuse()
        # ``dataset`` is our own option — never forward it to the SDK ctor.
        client_options = {k: v for k, v in (options or {}).items() if k != "dataset"}
        try:
            self._client = langfuse.Langfuse(**client_options)
        except Exception as exc:
            raise ConfigError(f"invalid Langfuse configuration: {exc}") from exc
        self._dataset = dataset

    async def load(self) -> list[DatasetRow]:
        return await asyncio.to_thread(self._fetch)

    def _fetch(self) -> list[DatasetRow]:
        dataset = self._client.get_dataset(self._dataset)
        rows: list[DatasetRow] = []
        for index, item in enumerate(dataset.items, start=1):
            raw = self._item_to_raw(item)
            rows.append(_parse_row(raw, index=index, source=f"langfuse:{self._dataset}"))
        return rows

    @staticmethod
    def _item_to_raw(item: Any) -> dict[str, Any]:
        """Map a Langfuse dataset item onto the harness row schema.

        ``input`` may already be a messages list, a ``{"messages": [...]}`` dict,
        or a bare string (wrapped as a single user turn). ``expected_output``
        becomes ``expected``.
        """
        input_value = getattr(item, "input", None)
        if isinstance(input_value, list):
            messages = input_value
        elif isinstance(input_value, dict) and isinstance(input_value.get("messages"), list):
            messages = input_value["messages"]
        else:
            content = "" if input_value is None else str(input_value)
            messages = [{"role": "user", "content": content}]
        expected = getattr(item, "expected_output", None)
        raw: dict[str, Any] = {
            "messages": messages,
            "expected": "" if expected is None else str(expected),
        }
        item_id = getattr(item, "id", None)
        if item_id is not None:
            raw["id"] = str(item_id)
        return raw


def _langfuse_dataset_source_factory(options: dict[str, Any]) -> DatasetSource:
    dataset = options.get("dataset")
    if not dataset:
        raise ConfigError("langfuse dataset source requires a 'dataset' option (the dataset name)")
    return LangfuseDatasetSource(dataset=str(dataset), options=options)


dataset_source_registry.register("langfuse", _langfuse_dataset_source_factory)
