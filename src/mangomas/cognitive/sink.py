"""CognitiveSignal sinks: JSONL (default) and optional HTTP POST.

Failures at the HTTP inner sink are isolated so a JSONL write still lands.
Agent ``handle`` additionally swallows sink errors so dispatch is unchanged.

Two controls the envelope has always described and nothing enforced
(ADR-0032):

**Expiry.** ``CognitiveSignal`` carries ``created_at``/``expires_at``/
``ttl_seconds``, validates that they agree, and offers ``is_expired()`` — which
had no caller anywhere in ``src/``. Sinks now refuse a signal past its TTL, so
a stale envelope cannot be presented as current.

**Replay.** ``signal_id`` gives uniqueness but is not a nonce: nothing recorded
which ids had been seen, so re-emitting the same envelope landed as many times
as it was sent. Sinks now drop a repeat and the HTTP sink sends an
``Idempotency-Key``, so a retry at any layer is recognisable as one delivery.

Neither control is a substitute for a signature. The envelope is unsigned, so a
*forged* envelope remains indistinguishable from a genuine one; these stop
accidental duplication and stale reuse, not an adversary. See ADR-0032.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import httpx

from mangomas.cognitive.constants import JSONL_FILENAME

if TYPE_CHECKING:
    from mango_contracts import CognitiveSignal
    from mangomas.config.signal import SignalSettings

logger = logging.getLogger(__name__)


# Header the HTTP sink sends so an ingest endpoint can collapse a retry.
IDEMPOTENCY_HEADER: Final[str] = "Idempotency-Key"

# How many recently-seen signal ids a sink remembers. Bounded on purpose: an
# unbounded set in a long-lived process is a memory leak wearing a security
# control's clothes. Sized generously relative to any realistic retry window —
# a replay older than this is accepted again, which is why expiry, not this, is
# the primary control.
DEFAULT_REPLAY_GUARD_ENTRIES: Final[int] = 4096


class ExpiredSignalError(ValueError):
    """Raised when a sink is handed an envelope past its TTL.

    A ``ValueError`` rather than a ``MangomasError``: this is a contract
    violation by the *producer*, not a runtime failure with an HTTP status.
    ``emit_agent_signal`` already contains every sink exception, so this never
    reaches a dispatch caller.
    """


class ReplayGuard:
    """Remembers recently-seen signal ids, oldest evicted first.

    Deliberately not a plain ``set``: ``max_entries`` bounds the memory a
    long-running process spends on this, and an ``OrderedDict`` gives the
    eviction order for free.
    """

    def __init__(self, max_entries: int = DEFAULT_REPLAY_GUARD_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._seen: OrderedDict[str, None] = OrderedDict()

    def seen(self, signal_id: str) -> bool:
        """**Reserve** *signal_id* and return whether it had already been seen.

        Reserving inside the check — rather than recording after a successful
        write — is what makes two concurrent emits of one id resolve to a
        single write: there is no await between the test and the insert, so the
        pair is atomic on the event loop. The caller owes a :meth:`release` if
        the work it reserved for then fails.
        """
        if signal_id in self._seen:
            # Do NOT refresh recency here: a replayed id must not be able to
            # keep itself alive in the window and evict genuine ids.
            return True
        self._seen[signal_id] = None
        if len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        return False

    def release(self, signal_id: str) -> None:
        """Undo a reservation whose write did not land.

        Without this, a guard that reserves before writing turns a *transient*
        failure — a full disk, a 503, a dropped connection — into permanent
        loss: the id is remembered, so the caller's retry is read as a replay
        and dropped silently. A control against duplication must not become a
        cause of disappearance. Idempotent, so a double release is harmless.
        """
        self._seen.pop(signal_id, None)


def _reject_if_expired(signal: CognitiveSignal) -> None:
    """Raise :class:`ExpiredSignalError` when *signal* is past its TTL."""
    if signal.is_expired():
        raise ExpiredSignalError(
            f"signal {signal.signal_id} expired at {signal.expires_at.isoformat()}"
        )


@runtime_checkable
class CognitiveSignalSink(Protocol):
    """Append-only destination for a validated cognitive envelope."""

    async def emit(self, signal: CognitiveSignal) -> None:
        """Persist or forward *signal*. Must not grant capability."""
        ...


class JsonlCognitiveSink:
    """Write one compact JSON object per line under ``path``."""

    def __init__(self, path: Path, *, replay_guard: ReplayGuard | None = None) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._replay_guard = replay_guard if replay_guard is not None else ReplayGuard()

    async def emit(self, signal: CognitiveSignal) -> None:
        _reject_if_expired(signal)
        if self._replay_guard.seen(str(signal.signal_id)):
            logger.debug(
                "cognitive signal already written; dropping replay",
                extra={"event": "cognitive_sink_replay", "signal_id": str(signal.signal_id)},
            )
            return
        # One write() of ``line + newline`` so POSIX O_APPEND stays atomic
        # under concurrent ``asyncio.to_thread`` workers.
        line = (
            json.dumps(signal.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        try:
            async with self._lock:
                await asyncio.to_thread(self._append, line)
        except BaseException:
            # The reservation is only valid if the write landed. Releasing it
            # keeps a retry of a transient failure from being read as a replay.
            self._replay_guard.release(str(signal.signal_id))
            raise
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

    def __init__(
        self,
        url: str,
        timeout_seconds: float,
        *,
        replay_guard: ReplayGuard | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._replay_guard = replay_guard if replay_guard is not None else ReplayGuard()

    async def emit(self, signal: CognitiveSignal) -> None:
        _reject_if_expired(signal)
        if self._replay_guard.seen(str(signal.signal_id)):
            logger.debug(
                "cognitive signal already posted; dropping replay",
                extra={"event": "cognitive_sink_replay", "signal_id": str(signal.signal_id)},
            )
            return
        payload = signal.model_dump(mode="json")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    json=payload,
                    # So an ingest endpoint can collapse a retry that happened
                    # below this layer (a proxy, a client-side retry) and that
                    # the in-process guard above therefore never sees.
                    headers={IDEMPOTENCY_HEADER: str(signal.signal_id)},
                )
                response.raise_for_status()
        except BaseException:
            # A 5xx, a timeout or a dropped connection must not poison the
            # guard: the envelope was not delivered, so a retry has to be
            # allowed through. The Idempotency-Key above is what protects the
            # endpoint if the request in fact arrived.
            self._replay_guard.release(str(signal.signal_id))
            raise
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
