"""Convert a Mango-Mas eval dataset (JSONL) into the ``inputs``-shaped JSONL
that the ``ianshank/Agents`` harness (``eval-harness``) expects.

Mango-Mas row:  ``{"id", "messages": [{"role","content"}], "expected", "metadata"}``
Agents row:     ``{"id", "inputs": {"messages": [...], "agent"?}, "expected", "metadata"}``

Nesting the messages under ``inputs.messages`` lets
:func:`mango_bridge.to_messages` pass them through verbatim, so multi-turn rows
round-trip losslessly. Raw Mango-Mas JSONL will NOT load meaningfully unmodified
— the Agents loader leaves ``inputs`` empty for a ``{"messages": ...}`` row — so
this conversion is a hard prerequisite, not a convenience.

Validation mirrors Mango-Mas's own loader (``mangomas.eval.dataset``) without
importing it: rows fail *closed* at conversion time (bad ``messages`` /
``expected`` / ``metadata`` / message entries) rather than producing Agents
JSONL that later fails in harder-to-debug ways.

Usage::

    python convert_dataset.py SRC.jsonl DST.jsonl [--agent chat]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Roles Mango-Mas's Message model accepts (Literal in mangomas.core.agent).
_VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})


def _validate_message(entry: Any, *, row_id: str) -> None:
    if (
        not isinstance(entry, dict)
        or entry.get("role") not in _VALID_ROLES
        or not isinstance(entry.get("content"), str)
    ):
        raise ValueError(f"row {row_id!r}: malformed message entry: {entry!r}")


def convert_row(row: Any, *, index: int = 1, agent: str | None = None) -> dict[str, Any]:
    """Map a single Mango-Mas row to an Agents ``EvalItem``-shaped row.

    ``index`` (1-based) seeds a stable ``row-<index>`` id when the source omits
    ``id``, mirroring Mango-Mas's own ``DatasetRow`` normalisation so every
    report row stays addressable.
    """
    if not isinstance(row, dict):
        raise ValueError(f"expected a JSON object, got {type(row).__name__}")
    row_id = str(row.get("id") or f"row-{index}")
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"row {row_id!r}: 'messages' must be a non-empty list")
    for entry in messages:
        _validate_message(entry, row_id=row_id)
    expected = row.get("expected")
    if not isinstance(expected, str):
        raise ValueError(
            f"row {row_id!r}: 'expected' must be a string, got {type(expected).__name__}"
        )
    metadata = row.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(
            f"row {row_id!r}: 'metadata' must be an object, got {type(metadata).__name__}"
        )
    inputs: dict[str, Any] = {"messages": messages}
    if agent:
        inputs["agent"] = agent
    out: dict[str, Any] = {"id": row_id, "inputs": inputs, "expected": expected}
    if metadata:
        out["metadata"] = metadata
    return out


def convert_lines(lines: Iterable[str], *, agent: str | None = None) -> list[dict[str, Any]]:
    """Convert JSONL text lines to Agents rows, skipping blank lines.

    Every validation error is annotated with its 1-based line number so a bad
    row in a large dataset is trivially locatable.
    """
    rows: list[dict[str, Any]] = []
    skipped = 0
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            skipped += 1
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {index}: malformed JSON: {exc}") from exc
        try:
            rows.append(convert_row(raw, index=index, agent=agent))
        except ValueError as exc:
            raise ValueError(f"line {index}: {exc}") from exc
    if skipped:
        logger.debug(
            "Skipped blank dataset lines",
            extra={"event": "dataset_blank_lines", "skipped": skipped},
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert Mango-Mas JSONL to Agents JSONL.")
    parser.add_argument("src", type=Path, help="Mango-Mas JSONL source path")
    parser.add_argument("dst", type=Path, help="Agents JSONL destination path")
    parser.add_argument("--agent", default=None, help="Inject inputs.agent for every row")
    args = parser.parse_args(argv)

    rows = convert_lines(args.src.read_text(encoding="utf-8").splitlines(), agent=args.agent)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(payload, encoding="utf-8")
    logger.info(
        "Dataset converted",
        extra={"event": "dataset_converted", "rows": len(rows), "dst": str(args.dst)},
    )
    print(f"Converted {len(rows)} rows -> {args.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
