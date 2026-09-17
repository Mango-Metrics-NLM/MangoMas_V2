# ADR-0032: Cognitive-signal expiry and replay resistance are enforced

- **Status:** Accepted
- **Date:** 2026-09-16
- **Extends:** ADR-0029 (cognitive execution boundary)
- **Spec:** the *signal expiry and replay resistance* spec — to be written,
  deliberately unnumbered until it exists (see ADR-0031 on why)
- **Source:** `docs/analysis/20260916-workflow-governance-audit.md` §5, §8

## Context

`CognitiveSignal` 1.1.0 has carried `created_at`, `expires_at` and
`ttl_seconds` since it shipped, validates that they agree within five seconds,
rejects naive datetimes, and offers `is_expired()` and `is_prompt_eligible()`.

None of it was enforced. `grep -rn "is_expired|is_prompt_eligible|ttl_seconds"
src/` returned **zero hits outside the contracts package**: expiry was a
property of the record that no code in this repository read. The producer never
passed `ttl_seconds`, so every envelope used the envelope's own 24-hour default
and the lifetime was not configurable at all. Both sinks appended or POSTed
unconditionally, so re-emitting the same envelope landed as many times as it
was sent.

This is the clearest instance of the pattern the audit named: **a control
documented in the type and absent from the system.**

## Decision

**1. Sinks refuse an expired envelope.** `JsonlCognitiveSink` and
`HttpCognitiveSink` call `is_expired()` before doing any work and raise
`ExpiredSignalError`. The HTTP sink checks *before* the request, not after, so
a stale envelope never reaches the ingest endpoint — the party the control
exists to protect.

**2. Lifetime is an operator tunable.** `MANGOMAS_SIGNAL__TTL_SECONDS`
(default 86400, max 30 days) reaches `CognitiveSignal.create`.
`config/signal.py` mirrors the contracts bounds rather than importing them:
`SignalSettings` is constructed on every `Settings` build, including when
signal emission is off, and importing `mango_contracts` there would break the
flag-off guarantee that nothing loads the contracts package. The copy is
deliberate; a test pins it against the real envelope so the two cannot drift.

**3. Sinks drop a repeated `signal_id`.** `ReplayGuard` is a bounded,
insertion-ordered set of recently-seen ids. A hit **does not refresh recency**:
if it did, a caller replaying one envelope in a loop could flush every genuine
id out of the window and then replay those freely.

**4. The HTTP sink sends `Idempotency-Key: <signal_id>`.** The in-process guard
cannot see a retry that happens below this layer — a proxy, a client-side
retry — so the header lets the ingest endpoint collapse it.

## Consequences

**What this buys.** A stale envelope cannot be presented as current, an
accidental double-emit lands once, and a retry is recognisable as one delivery.

**What it does not buy, stated plainly.** The envelope is **unsigned**. These
controls stop accidental duplication and stale reuse; they do not stop an
adversary, who can mint a fresh `signal_id` and a current `created_at` at will.
`policy_snapshot_hash` remains a self-computed provenance label, not an
attestation (see the audit's §3b and the D0 documentation milestone). A
verifier that treats any of this as authentication is mistaken, and the sink
module's docstring says so at the point a reader would form that belief.

**The bounded window is a deliberate trade.** `ReplayGuard` holds 4096 ids by
default and evicts oldest-first, so a replay older than the window is accepted
again. An unbounded set in a long-lived process is a memory leak wearing a
security control's clothes. Expiry, not the guard, is the primary control;
the guard is the cheap defence against the common case.

**A behaviour change worth naming.** Emitting the same envelope twice now
writes one line, not two. `test_jsonl_concurrent_appends` was rewritten to use
distinct `signal_id`s — it previously reused one envelope, which stopped
proving anything about concurrent appends once the sink learned to deduplicate.

## Alternatives considered

- **Enforce expiry in the producer instead.** Rejected: the producer stamps
  `created_at` itself, so a freshly built envelope is never expired there. The
  boundary that benefits is the one that *accepts* a signal.
- **A plain unbounded `set` for seen ids.** Rejected on memory grounds; see
  Consequences.
- **Refresh recency on a hit (a true LRU).** Rejected: it hands a replaying
  caller the ability to evict genuine ids, inverting the control.
- **Signing the envelope.** The real answer to forgery, and deliberately out of
  scope: signing here without a verifier in the sibling harness is ceremony.
  Deferred in the governance hardening plan against ADR-0029.
