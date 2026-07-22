# Spec-0011: Request backpressure

- **Status:** Implemented
- **Linked ADR:** ADR-0015
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

The service fronts a single slow upstream (LM Studio / Vertex) from a small
container, but nothing bounds request size or concurrency: a large unbounded
`AgentRequest.messages` POST is buffered into memory, and a burst all hits the
one upstream at once. Cloud Run's `containerConcurrency` protects neither memory
nor the upstream. This spec adds two `APISettings`-gated guards — a max-body-size
check (413) and an in-flight concurrency cap (503) — with effectively-off
defaults.

## Requirements

- **Body-size guard**: reject a request whose `Content-Length` exceeds
  `max_body_bytes` with `413` before Starlette buffers the body.
- **Concurrency guard**: when `max_concurrent_requests` in-flight requests are
  already being served, reject further requests immediately with `503` (reject,
  do not queue), so a slow upstream cannot build an unbounded backlog.
- Must remain **additive & default-OFF**: both limits default to `0` (off) →
  the middleware is not installed, behaviour byte-identical.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_API__MAX_BODY_BYTES` | `0` | Max request body bytes (`0` = unlimited/off) |
| `MANGOMAS_API__MAX_CONCURRENT_REQUESTS` | `0` | Max in-flight requests (`0` = unbounded/off) |

New `DEFAULT_API_MAX_BODY_BYTES` / `DEFAULT_API_MAX_CONCURRENT_REQUESTS` constants.

## Protocol / contract impact

- New/changed protocols: _none_.
- New error types: _none_ — the middleware returns a JSON envelope directly
  (`{"error", "message"}`), matching the app's error shape, without a
  `MangomasError`.
- Registry additions: _none_.
- New `MaxBodySizeMiddleware` / `ConcurrencyLimitMiddleware` in `api/middleware.py`.

## Backwards-compatibility

- Both limits `0` (default) → middleware not installed; every route byte-identical.
- No protected-path edit.

## Test plan

- Unit (`tests/test_backpressure.py`, api floor 95): oversized body → 413,
  under-limit → 200; a blocking route with `max_concurrent=1` → a concurrent
  second request gets 503, then the first completes 200 (via `httpx.AsyncClient`);
  limits `0` → middleware absent (behaviour identical).
- Size/limit constants in `tests/constants.py`.
- Coverage: maintain the 95% global gate and the api floor.

## Acceptance criteria

- [x] Oversized `Content-Length` → 413 before buffering (test proves it).
- [x] Second concurrent request beyond the cap → 503; the first still completes.
- [x] Limits `0` → no middleware installed, behaviour identical.
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; ADR-0015 added.
