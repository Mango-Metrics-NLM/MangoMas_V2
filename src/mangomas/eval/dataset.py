"""Dataset loader for the evaluation harness.

Datasets are JSONL — one record per line. Each record has the shape:

.. code-block:: json

    {
      "id": "row-1",
      "messages": [{"role": "user", "content": "..."}],
      "expected": "...",
      "metadata": {}
    }

``id`` is optional (auto-assigned if absent); ``metadata`` is optional.
All disk I/O is performed via :func:`asyncio.to_thread` per the project's
async-I/O convention.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.core.agent import Message
from mangomas.errors import MangomasError

logger = logging.getLogger(__name__)

# Preview length for the malformed-``messages`` value echoed in error detail —
# shorter than a full error detail because the raw list can be large.
_MALFORMED_PREVIEW_TRUNCATE: int = 120


class DatasetError(MangomasError):
    """Raised for malformed dataset rows or missing files."""

    code = "eval_dataset_error"


@dataclass
class DatasetRow:
    """A single evaluation row.

    ``id`` defaults to ``row-<index>`` when the source line omits it so
    every row in a report has a stable identifier even for legacy datasets.
    """

    id: str
    messages: list[Message]
    expected: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_row(raw: dict[str, Any], *, index: int, source: str) -> DatasetRow:
    """Convert a parsed JSON object into a :class:`DatasetRow`."""
    if not isinstance(raw, dict):  # pragma: no cover - defensive
        raise DatasetError(f"{source} row {index}: expected JSON object, got {type(raw).__name__}")
    raw_messages = raw.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise DatasetError(
            f"{source} row {index}: 'messages' must be a non-empty list",
            detail=str(raw_messages)[:_MALFORMED_PREVIEW_TRUNCATE],
        )
    try:
        messages = [Message(**m) for m in raw_messages]
    except Exception as exc:
        raise DatasetError(
            f"{source} row {index}: invalid message entry",
            detail=str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
        ) from exc
    expected = raw.get("expected")
    if not isinstance(expected, str):
        raise DatasetError(
            f"{source} row {index}: 'expected' must be a string",
            detail=f"got {type(expected).__name__}",
        )
    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise DatasetError(
            f"{source} row {index}: 'metadata' must be an object",
            detail=f"got {type(metadata).__name__}",
        )
    row_id = raw.get("id") or f"row-{index}"
    return DatasetRow(
        id=str(row_id),
        messages=messages,
        expected=expected,
        metadata=metadata,
    )


def _read_jsonl(path: Path) -> list[DatasetRow]:
    """Synchronous JSONL reader — invoked from a worker thread."""
    if not path.is_file():
        raise DatasetError(f"Dataset file not found: {path}")
    rows: list[DatasetRow] = []
    text = path.read_text(encoding="utf-8")
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise DatasetError(
                f"{path} line {index}: malformed JSON",
                detail=str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
            ) from exc
        rows.append(_parse_row(raw, index=index, source=str(path)))
    return rows


async def load_jsonl(path: Path | str) -> list[DatasetRow]:
    """Load a JSONL dataset off the event loop.

    Any per-row validation error raises :class:`DatasetError`. Empty lines
    are ignored so reformatted files don't blow up. The caller is expected
    to handle :class:`DatasetError` (the CLI surfaces it as exit code 1).
    """
    target = Path(path)
    logger.debug(
        "Loading eval dataset",
        extra={"event": "eval_dataset_load_start", "path": str(target)},
    )
    rows = await asyncio.to_thread(_read_jsonl, target)
    logger.info(
        "Eval dataset loaded",
        extra={
            "event": "eval_dataset_loaded",
            "path": str(target),
            "row_count": len(rows),
        },
    )
    return rows
