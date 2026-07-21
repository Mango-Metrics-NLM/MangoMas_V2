"""JSON-file sink — serialise the report (and gate verdict) to a file.

Refactors the CLI's former inline ``dataclasses.asdict + json.dumps`` block into
a reusable sink. The write runs under ``asyncio.to_thread`` so synchronous file
I/O never blocks the event loop. When a :class:`GateResult` is supplied it is
attached under a top-level ``"gate"`` key, so CI artifacts capture the verdict.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mangomas.errors import ConfigError
from mangomas.eval.sink import Sink
from mangomas.eval.sink_registry import sink_registry

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport

logger = logging.getLogger(__name__)


class JsonFileSink:
    """Write the report as pretty-printed JSON to ``path``."""

    name = "json_file"

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        payload: dict[str, Any] = dataclasses.asdict(report)
        if gate_result is not None:
            payload["gate"] = dataclasses.asdict(gate_result)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._write, text)
        logger.debug(
            "Eval report written",
            extra={"event": "eval_sink_json_file", "path": str(self._path)},
        )

    def _write(self, text: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(text, encoding="utf-8")


def _json_file_factory(options: dict[str, Any]) -> Sink:
    path = options.get("path")
    if not path or not isinstance(path, str):
        raise ConfigError("json_file sink requires a string 'path' option")
    return JsonFileSink(path=path)


sink_registry.register("json_file", _json_file_factory)
