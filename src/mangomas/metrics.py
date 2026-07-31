"""Application metric instruments (opt-in; ADR-0013).

Thin record helpers over OpenTelemetry counters/histogram. Instruments are
created **lazily** from the global ``MeterProvider`` on first record, so they
bind to the real provider installed by :func:`mangomas.telemetry.configure_metrics`
(called in the app lifespan) rather than the no-op default captured at import.
Until metrics are enabled the global provider is the OTel no-op, so every record
call costs nothing — callers record unconditionally.
"""

from __future__ import annotations

from threading import Lock

from mangomas.telemetry import get_meter

_METER_NAME = "mangomas.metrics"

# Instrument names (single source of truth; also imported by tests).
AGENT_INVOCATIONS = "mangomas.agent.invocations"
AGENT_ERRORS = "mangomas.agent.errors"
AGENT_DURATION = "mangomas.agent.duration"


class _Instruments:
    """Counters + histogram bound to the current global ``MeterProvider``."""

    def __init__(self) -> None:
        meter = get_meter(_METER_NAME)
        self.invocations = meter.create_counter(
            AGENT_INVOCATIONS,
            unit="1",
            description="Agent invocations, by agent and status",
        )
        self.errors = meter.create_counter(
            AGENT_ERRORS,
            unit="1",
            description="Agent invocation errors, by agent and error code",
        )
        self.duration = meter.create_histogram(
            AGENT_DURATION,
            unit="s",
            description="Agent invocation wall-clock duration",
        )


class _InstrumentState:
    """Holds the lazily-created instruments — avoids a module-level ``global``."""

    instruments: _Instruments | None = None


_state = _InstrumentState()
# Guards first-record instrument creation. Record helpers may be hit from many
# threads at once (e.g. ``asyncio.to_thread`` workers); double-checked locking
# keeps the post-init fast path lock-free while guaranteeing exactly one
# ``_Instruments`` is ever built (mirrors ``telemetry._lock``; spec 0014 / D7).
_instruments_lock = Lock()


def _instruments() -> _Instruments:
    instruments = _state.instruments
    if instruments is None:
        with _instruments_lock:
            instruments = _state.instruments
            if instruments is None:
                instruments = _Instruments()
                _state.instruments = instruments
    return instruments


def record_agent_invocation(agent: str, status: str) -> None:
    """Increment the agent-invocation counter (attributes: ``agent``, ``status``)."""
    _instruments().invocations.add(1, {"agent": agent, "status": status})


def record_agent_error(agent: str, code: str) -> None:
    """Increment the agent-error counter (attributes: ``agent``, ``code``)."""
    _instruments().errors.add(1, {"agent": agent, "code": code})


def record_agent_duration(agent: str, seconds: float) -> None:
    """Record the agent-invocation duration histogram (attribute: ``agent``)."""
    _instruments().duration.record(seconds, {"agent": agent})
