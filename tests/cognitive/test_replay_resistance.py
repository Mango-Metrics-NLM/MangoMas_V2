"""Expiry and replay controls must be enforced, not merely modelled (ADR-0032).

``CognitiveSignal`` has carried ``created_at`` / ``expires_at`` / ``ttl_seconds``
since 1.1.0, validates that they agree within five seconds, and offers
``is_expired()``. Before this, ``grep -rn "is_expired" src/`` returned **zero
hits**: expiry was a property of the record that no code read, and both sinks
appended or POSTed unconditionally, so re-emitting the same envelope landed as
many times as it was sent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from tests.constants import SIGNAL_MOCK_INGEST_URL
from tests.mango_contracts.constants import envelope_base

from mango_contracts import CognitiveSignal
from mango_contracts.cognitive_signal import DEFAULT_TTL_SECONDS, MAX_TTL_SECONDS
from mangomas.cognitive.constants import JSONL_FILENAME
from mangomas.cognitive.producer import build_signal
from mangomas.cognitive.sink import (
    ExpiredSignalError,
    HttpCognitiveSink,
    JsonlCognitiveSink,
    ReplayGuard,
)
from mangomas.config import SignalSettings
from mangomas.config.signal import DEFAULT_SIGNAL_TTL_SECONDS, MAX_SIGNAL_TTL_SECONDS
from mangomas.core.agent import AgentRequest, Message


def _signal(**overrides: object) -> CognitiveSignal:
    return CognitiveSignal.model_validate({**envelope_base(), **overrides})


def _expired_signal() -> CognitiveSignal:
    """An envelope whose TTL has already elapsed, built the way a producer would.

    ``created_at``/``expires_at``/``ttl_seconds`` must agree within five
    seconds, so an expired envelope is made by back-dating creation rather than
    by shortening ``expires_at`` — which the model would reject outright.
    """
    created = datetime.now(UTC) - timedelta(hours=2)
    return _signal(
        created_at=created.isoformat(),
        expires_at=(created + timedelta(hours=1)).isoformat(),
        ttl_seconds=3600,
    )


async def test_jsonl_sink_refuses_an_expired_signal(tmp_path: Path) -> None:
    """An envelope past its TTL must not be written."""
    path = tmp_path / JSONL_FILENAME
    sink = JsonlCognitiveSink(path)

    with pytest.raises(ExpiredSignalError):
        await sink.emit(_expired_signal())

    assert not path.exists()


async def test_jsonl_sink_still_writes_a_live_signal(tmp_path: Path) -> None:
    """The other direction: expiry enforcement must not refuse everything.

    Without this, a sink that raised unconditionally would pass the test above
    while silently switching cognitive emission off.
    """
    path = tmp_path / JSONL_FILENAME
    sink = JsonlCognitiveSink(path)

    await sink.emit(_signal())

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


async def test_jsonl_sink_writes_a_replayed_signal_once(tmp_path: Path) -> None:
    """The same ``signal_id`` twice is one line, not two."""
    path = tmp_path / JSONL_FILENAME
    sink = JsonlCognitiveSink(path)
    signal = _signal()

    await sink.emit(signal)
    await sink.emit(signal)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


async def test_two_distinct_signals_both_write(tmp_path: Path) -> None:
    """Dedupe must key on identity, not collapse every signal into one.

    The discriminating direction: a sink that wrote only the first envelope it
    ever saw would pass the replay test above.
    """
    path = tmp_path / JSONL_FILENAME
    sink = JsonlCognitiveSink(path)

    await sink.emit(_signal())
    await sink.emit(_signal(signal_id="11111111-1111-4111-8111-111111111111"))

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


@respx.mock
async def test_http_sink_sends_an_idempotency_key() -> None:
    """A retry at any layer must be recognisable as the same delivery."""
    route = respx.post(SIGNAL_MOCK_INGEST_URL).mock(return_value=httpx.Response(202))
    signal = _signal()

    await HttpCognitiveSink(SIGNAL_MOCK_INGEST_URL, 5.0).emit(signal)

    assert route.calls[0].request.headers["Idempotency-Key"] == str(signal.signal_id)


@respx.mock
async def test_http_sink_does_not_repost_a_replayed_signal() -> None:
    """Dedupe covers the network sink too, not only the file one."""
    route = respx.post(SIGNAL_MOCK_INGEST_URL).mock(return_value=httpx.Response(202))
    sink = HttpCognitiveSink(SIGNAL_MOCK_INGEST_URL, 5.0)
    signal = _signal()

    await sink.emit(signal)
    await sink.emit(signal)

    assert route.call_count == 1


def test_replay_guard_is_bounded() -> None:
    """The seen-id set must not grow without limit.

    An unbounded set in a long-lived process is a memory leak wearing a
    security control's clothes. Oldest ids are evicted first, so a replay of a
    very old signal is accepted again — a deliberate trade, and the reason
    expiry (above) is the primary control rather than this.
    """
    guard = ReplayGuard(max_entries=2)

    assert guard.seen("a") is False
    assert guard.seen("b") is False
    assert guard.seen("a") is True, "a replay inside the window must be caught"

    guard.seen("c")  # evicts the oldest entry, which is "a"

    assert guard.seen("a") is False, "the oldest id should have been evicted"
    assert guard.seen("c") is True, "the newest id must still be remembered"


def test_a_replay_does_not_refresh_its_own_recency() -> None:
    """Re-presenting an id must not let it evict genuine ids.

    If a hit moved the entry to the back of the queue, a caller replaying one
    envelope in a loop could flush every real id out of a bounded window and
    then replay those freely. The guard records recency on *insert* only.
    """
    guard = ReplayGuard(max_entries=2)
    guard.seen("a")
    guard.seen("b")

    for _ in range(5):
        assert guard.seen("a") is True

    guard.seen("c")  # "a" is still the oldest despite the repeated hits

    assert guard.seen("b") is True, "b was evicted by a replay refreshing itself"


# ── TTL is an operator tunable, and mirrors the envelope's own bounds ────────


def test_signal_ttl_default_and_bound_match_the_envelope() -> None:
    """``config/signal.py`` mirrors the contracts constants; pin the mirror.

    ``SignalSettings`` deliberately does not import ``mango_contracts`` — it is
    built on every ``Settings`` construction, including when signal emission is
    off, and importing the contracts package there would break the flag-off
    guarantee. The copy is intentional; the two silently disagreeing would not
    be.
    """
    assert DEFAULT_SIGNAL_TTL_SECONDS == DEFAULT_TTL_SECONDS
    assert MAX_SIGNAL_TTL_SECONDS == MAX_TTL_SECONDS


def test_producer_stamps_the_configured_ttl() -> None:
    """The setting must reach the envelope, not merely exist."""
    settings = SignalSettings(ttl_seconds=90)
    request = AgentRequest(messages=[Message(role="user", content="plan it")])

    signal = build_signal(
        agent_name="planner",
        content='{"goal": "ship", "steps": ["do it"]}',
        request=request,
        settings=settings,
        producer_version="0.0.0-test",
    )

    assert signal.ttl_seconds == 90
    assert (signal.expires_at - signal.created_at).total_seconds() == 90


def test_replay_guard_rejects_a_useless_bound() -> None:
    """A zero or negative window would silently disable the guard.

    Failing loudly at construction beats a guard that exists, is configured
    into uselessness, and keeps reporting that every signal is new.
    """
    with pytest.raises(ValueError, match="max_entries"):
        ReplayGuard(max_entries=0)


@respx.mock
async def test_http_sink_refuses_an_expired_signal() -> None:
    """Expiry is enforced before the request is made, not after.

    Enforcing it after would still leak a stale envelope to the ingest
    endpoint, which is the party the control exists to protect.
    """
    route = respx.post(SIGNAL_MOCK_INGEST_URL).mock(return_value=httpx.Response(202))

    with pytest.raises(ExpiredSignalError):
        await HttpCognitiveSink(SIGNAL_MOCK_INGEST_URL, 5.0).emit(_expired_signal())

    assert route.call_count == 0


# ── A failed write must not poison the guard (Copilot review, PR #64) ────────


async def test_a_failed_jsonl_write_does_not_block_the_retry(tmp_path: Path) -> None:
    """A transient disk failure must not turn dedupe into permanent loss.

    The guard reserves the id *before* writing, which is what makes two
    concurrent emits resolve to one write. If the write then fails and the
    reservation stands, the caller's retry is read as a replay and dropped —
    a control against duplication becoming a cause of disappearance.
    """
    path = tmp_path / JSONL_FILENAME
    sink = JsonlCognitiveSink(path)
    signal = _signal()
    calls: list[int] = []
    real_append = sink._append

    def _fail_once(line: str) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("no space left on device")
        real_append(line)

    sink._append = _fail_once  # type: ignore[method-assign]

    with pytest.raises(OSError, match="no space left"):
        await sink.emit(signal)

    await sink.emit(signal)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1, "the retry did not land"


async def test_a_successful_jsonl_write_still_blocks_a_replay(tmp_path: Path) -> None:
    """Releasing on failure must not release on success.

    The discriminating direction: a sink that released unconditionally would
    pass the test above while silently disabling dedupe entirely.
    """
    path = tmp_path / JSONL_FILENAME
    sink = JsonlCognitiveSink(path)
    signal = _signal()

    await sink.emit(signal)
    await sink.emit(signal)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


@respx.mock
async def test_a_failed_http_post_does_not_block_the_retry() -> None:
    """Same for the network sink: a 5xx must leave the envelope retryable."""
    route = respx.post(SIGNAL_MOCK_INGEST_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(202)]
    )
    sink = HttpCognitiveSink(SIGNAL_MOCK_INGEST_URL, 5.0)
    signal = _signal()

    with pytest.raises(httpx.HTTPStatusError):
        await sink.emit(signal)

    await sink.emit(signal)

    assert route.call_count == 2, "the retry was swallowed as a replay"


def test_release_is_idempotent_and_reopens_the_id() -> None:
    """``release`` undoes a reservation and tolerates being called twice."""
    guard = ReplayGuard(max_entries=4)

    assert guard.seen("a") is False
    guard.release("a")
    guard.release("a")

    assert guard.seen("a") is False, "the id should be reservable again"
