"""Process-global telemetry state.

The base layer: every mutable module-level singleton the package owns lives
here and nowhere else. `configure_telemetry`, `configure_metrics` and
`build_scoped_tracer` all read or set these, so splitting them across the
consumer modules would create two sources of truth for one process.

That matters beyond tidiness. Two `_TelemetryState` instances would defeat
`configure_telemetry`'s idempotency guard, so a second call would install a
second logging handler and call `set_tracer_provider` twice — which OpenTelemetry
warns about and ignores, silently discarding the configured exporter. The
failure surfaces in production as "my Cloud Trace exporter did not take", not
as a test failure.

Rule this file exists to make checkable: no other module under
`mangomas.telemetry` may define a module-level `Lock()` or cache dict.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry import trace


class _TelemetryState:
    """Mutable singleton that tracks idempotency — avoids PLW0603."""

    configured: bool = False
    metrics_configured: bool = False


_state = _TelemetryState()


_lock = Lock()


_metrics_lock = Lock()


# Cache of dedicated scoped tracers keyed by (namespace, exporter) so repeated
# build_orchestrator calls reuse one TracerProvider/SpanProcessor instead of
# leaking a new one each time (matters most for the gcp Cloud Trace exporter).
_scoped_tracers: dict[tuple[str, str], trace.Tracer] = {}


_scoped_lock = Lock()
