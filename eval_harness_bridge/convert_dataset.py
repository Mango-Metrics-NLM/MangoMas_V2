"""Convert a Mango-Mas eval dataset (JSONL) into the ``inputs``-shaped JSONL
that the ``ianshank/Agents`` harness (``eval-harness``) expects.

Mango-Mas row:  ``{"id", "messages": [{"role","content"}], "expected", "metadata"}``
Agents row:     ``{"id", "inputs": {"messages": [...], "agent"?}, "expected", "metadata"}``

Nesting the messages under ``inputs.messages`` lets
:func:`mango_bridge.to_messages` pass them through verbatim, so multi-turn rows
round-trip losslessly. Raw Mango-Mas JSONL will NOT load meaningfully unmodified
— the Agents loader leaves ``inputs`` empty for a ``{"messages": ...}`` row — so
this conversion is a hard prerequisite, not a convenience.

Usage::

    python convert_dataset.py SRC.jsonl DST.jsonl [--agent chat]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def convert_row(row: dict[str, Any], *, agent: str | None = None) -> dict[str, Any]:
    """Map a single Mango-Mas row to an Agents ``EvalItem``-shaped row."""
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"row {row.get('id', '?')!r}: 'messages' must be a non-empty list")
    inputs: dict[str, Any] = {"messages": messages}
    if agent:
        inputs["agent"] = agent
    out: dict[str, Any] = {
        "id": row.get("id", ""),
        "inputs": inputs,
        "expected": row.get("expected", ""),
    }
    metadata = row.get("metadata")
    if metadata:
        out["metadata"] = metadata
    return out


def convert_lines(lines: Iterable[str], *, agent: str | None = None) -> list[dict[str, Any]]:
    """Convert JSONL text lines to Agents rows, skipping blank lines."""
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {index}: malformed JSON: {exc}") from exc
        try:
            rows.append(convert_row(raw, agent=agent))
        except ValueError as exc:
            raise ValueError(f"line {index}: {exc}") from exc
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
    print(f"Converted {len(rows)} rows -> {args.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
