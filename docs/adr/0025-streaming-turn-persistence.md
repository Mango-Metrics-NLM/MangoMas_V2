# ADR-0025: Streaming turn persistence — persist only on full drain

## Status

Accepted

## Context

`Orchestrator.dispatch` persists every turn via `ctx.repo.save_turn`, but
`stream_dispatch` never touched `ctx.repo` and the `/agents/{name}/stream`
route recorded no metrics — SSE conversations were invisible to `GET /history`,
to `SummarizeAgent`'s history window, to tenancy-scoped storage, and to the
agent invocation/error/duration instruments (spec-0025). Non-streaming agents
were also silently degraded to one unlabelled chunk. A streamed "turn" has no
single completion point unless we define one: the consumer can abandon
mid-drain, and the upstream can fail mid-stream.

## Decision

`stream_dispatch` accumulates chunks and persists the joined content as a turn
**only on a full drain** — the code after the token loop in the async
generator, never a `finally`. Abandonment (`GeneratorExit`) and upstream errors
persist nothing and record no invocation metric: full drain ⇔ persisted ⇔
counted. The degraded (buffered `handle()`) fallback logs a warning and
persists too — it is a full drain by construction. The SSE framing gains an
additive `metadata` event (agent, degraded flag, chunk count) before `done`;
token and `done` events stay byte-identical. A new
`Orchestrator.agent_supports_streaming(name)` query lets transport layers
label degraded streams; the stream iterator itself keeps yielding plain `str`.

## Consequences

### Positive

- `SummarizeAgent` and `/history` never see half-turns: a persisted streamed
  turn is always the complete answer the client received.
- Metrics parity with the invoke route; abandonment is deliberately invisible
  to the ok/error counters, so counts equal persisted turns.
- Degradation is observable (warning + `stream.degraded` span attribute +
  `metadata` SSE event) instead of silent.
- Existing SSE consumers are unaffected: the `metadata` event is a distinct
  event type they already ignore by construction.

### Negative / Trade-offs

- An abandoned stream leaves no trace in history or metrics even though tokens
  were generated and billed upstream — the cost of the half-turn guarantee.
- The full response is buffered in memory for the duration of the stream.

### Neutral

- `dispatch`/`stream_dispatch` signatures are unchanged; this is still a
  protected-path edit (path-based trailer obligation, ADR-0021).

## Alternatives Considered

- **Persist in a `finally` (partial turns on abandonment)** — rejected:
  history/summarization would ingest truncated assistant turns.
- **Inject a typed terminal object into the token stream** — rejected: widens
  the `AsyncIterator[str]` contract every consumer (harness `_traced_stream`,
  SSE route, CLI) relies on; out-of-band metadata is additive instead.

## References

- Code: `src/mangomas/core/orchestrator.py` (`_stream_agent`,
  `agent_supports_streaming`), `src/mangomas/api/routes/agents.py`
- Spec: `specs/0025-streaming-turn-persistence.md`
- Related ADRs: ADR-0013 (metrics seam), ADR-0021 (governance contract)
