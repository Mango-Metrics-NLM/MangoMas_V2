# ADR-0015: Request backpressure

## Status

Accepted

## Context

The service fronts one slow upstream from a small container with no bound on
request size or concurrency: an oversized `messages` POST is buffered into
memory, and a burst all hits the single upstream at once. Cloud Run's
`containerConcurrency` protects neither. We want cheap, default-OFF guards that
fit the env-driven / `APISettings` pattern and do not edit protected core.

## Decision

Add two `APISettings`-gated middlewares in `api/middleware.py`:
`MaxBodySizeMiddleware` rejects a request whose `Content-Length` exceeds
`max_body_bytes` with `413` before Starlette buffers it; `ConcurrencyLimitMiddleware`
tracks in-flight requests with a plain counter (race-free under asyncio's
single-threaded loop) and rejects the request beyond `max_concurrent_requests`
with `503` — **reject, do not queue**. Both are installed only when their limit
is `> 0`; the default `0` leaves them out entirely.

## Consequences

### Positive

- Bounds memory (oversized bodies) and protects the single upstream (burst
  rejection) — graceful degradation for a service fronting one slow dependency.
- Default `0` → middleware not installed, byte-identical; no protected-path edit;
  responses use the app's `{"error", "message"}` envelope.

### Negative / Trade-offs

- The body-size check reads `Content-Length`; a chunked request without one is
  not bounded (documented). Real JSON clients always send `Content-Length`.
- The concurrency counter is a soft limit — under streaming responses via
  `BaseHTTPMiddleware` the in-flight count may release when the response object
  is returned rather than when the body finishes. Acceptable for backpressure.

### Neutral

- Reject-don't-queue (503) is deliberate: queueing behind a slow upstream would
  build latency and memory; failing fast lets clients/LB retry or shed.

## Alternatives Considered

- **`asyncio.Semaphore`** — equivalent, but a counter with no `await` between
  check and increment is race-free and makes the reject-don't-queue semantics
  explicit (a semaphore's `acquire` blocks/queues by default).
- **Enforce body size mid-stream** — deferred: reading `Content-Length` rejects
  before buffering for all real clients; streaming enforcement adds complexity
  for a rare case.
- **Rely on Cloud Run `containerConcurrency`** — insufficient: it caps requests
  per container but does not bound body size or protect the upstream per-process.

## References

- Code: `src/mangomas/api/middleware.py` (`MaxBodySizeMiddleware`,
  `ConcurrencyLimitMiddleware`), `src/mangomas/api/app.py` (conditional install),
  `src/mangomas/config.py::APISettings`.
- Related: spec `specs/0011-request-backpressure.md`.
