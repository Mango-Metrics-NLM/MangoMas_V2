"""CognitiveSignal sinks: JSONL (default) and optional HTTP POST.

Failures at the HTTP inner sink are isolated so a JSONL write still lands.
Agent ``handle`` additionally swallows sink errors so dispatch is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx

from mangomas.cognitive.constants import JSONL_FILENAME

if TYPE_CHECKING:
    from mango_contracts import CognitiveSignal
    from mangomas.config.signal import SignalSettings

logger = logging.getLogger(__name__)


@runtime_checkable
class CognitiveSignalSink(Protocol):
    """Append-only destination for a validated cognitive envelope."""

    async def emit(self, signal: CognitiveSignal) -> None:
        """Persist or forward *signal*. Must not grant capability."""
        ...


class JsonlCognitiveSink:
    """Write one compact JSON object per line under ``path``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def emit(self, signal: CognitiveSignal) -> None:
        # One write() of ``line + newline`` so POSIX O_APPEND stays atomic
        # under concurrent ``asyncio.to_thread`` workers.
        line = (
            json.dumps(signal.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        async with self._lock:
            await asyncio.to_thread(self._append, line)
        logger.debug(
            "cognitive signal written",
            extra={"event": "cognitive_sink_jsonl", "path": str(self._path)},
        )

    def _append(self, line: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)


class HttpCognitiveSink:
    """POST the envelope JSON to a harness ingest URL when one exists."""

    def __init__(self, url: str, timeout_seconds: float) -> None:
        self._url = url
        self._timeout = timeout_seconds

    async def emit(self, signal: CognitiveSignal) -> None:
        payload = signal.model_dump(mode="json")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, json=payload)
            response.raise_for_status()
        logger.debug(
            "cognitive signal posted",
            extra={"event": "cognitive_sink_http", "url": self._url},
        )


class CompositeCognitiveSink:
    """Fan-out with per-inner isolation (mirrors eval sink fault isolation)."""

    def __init__(self, sinks: Sequence[CognitiveSignalSink]) -> None:
        self._sinks = tuple(sinks)

    async def emit(self, signal: CognitiveSignal) -> None:
        first_error: BaseException | None = None
        for sink in self._sinks:
            try:
                await sink.emit(signal)
            except Exception as exc:
                logger.exception(
                    "cognitive inner sink failed",
                    extra={"event": "cognitive_sink_inner_failed", "sink": type(sink).__name__},
                )
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def build_sink(settings: SignalSettings) -> CognitiveSignalSink:
    """JSONL always; HTTP is composed on top when ``http_url`` is set."""
    jsonl = JsonlCognitiveSink(Path(settings.dir) / JSONL_FILENAME)
    url = (settings.http_url or "").strip()
    if not url:
        return jsonl
    http = HttpCognitiveSink(url, settings.http_timeout_seconds)
    return CompositeCognitiveSink((jsonl, http))
