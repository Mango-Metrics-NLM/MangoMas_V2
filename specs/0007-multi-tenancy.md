# Spec-0007: Multi-tenancy

- **Status:** Phase 1 (storage isolation) — Implemented. Phase 2 (per-tenant
  `AgentSettings` at dispatch) — deferred.
- **Linked ADR:** ADR-0017 (storage isolation). _(Corrected from the original
  stub's "ADR-0009" — that number is already `telemetry-exporter-seam`.)_
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

`NEXT_STEPS.md` › "Long term" › *Multi-tenancy*: a single deployment should
serve multiple tenants with **tenant-scoped conversation storage** and
**per-tenant agent configuration**, without leaking state across tenants.

## Requirements

- A tenant identifier flows through the request path: an `X-Tenant-ID` header →
  a correlation-style `ContextVar`, reusing the `correlation.py` pattern.
- `TurnRepository` reads/writes are scoped by tenant via a **row filter**
  (`tenant` column + `WHERE tenant = ?`); no cross-tenant reads possible. The
  tenant is read from the `ContextVar` **inside** each repo method, so the
  `TurnRepository` Protocol signature is byte-identical (no new kwarg).
- **Phase 2 (deferred):** per-tenant `AgentSettings` via a tenant-keyed registry,
  resolved at dispatch — needs a dispatch-time settings-resolution decision.
- Must remain **additive & default-OFF**: absent/disabled tenant id → a single
  implicit `"default"` tenant, behaviour byte-identical to today.

## Config / env additions (sketch)

| Env var | Default | Purpose |
|---------|---------|---------|
| `MANGOMAS_TENANCY__ENABLED` | `false` | Enable tenant scoping |
| `MANGOMAS_TENANCY__HEADER` | `X-Tenant-ID` | Inbound tenant header |
| `MANGOMAS_TENANCY__DEFAULT` | `default` | Implicit tenant when none supplied |

## Protocol / contract impact

- `TurnRepository` signature **byte-identical** — the tenant is read from a
  `ContextVar` inside `save_turn` / `list_turns` (exactly like `correlation_id`),
  not passed as a kwarg (ADR-0017).
- New `TenancySettings` group; new `TenancyMiddleware` analogous to
  `AccessLogMiddleware`; new top-level `tenancy.py` (sibling of `correlation.py`).
- New error types: _none_.

## Backwards-compatibility

- Disabled → one implicit tenant; existing single-tenant deployments unaffected.
- Storage migration path documented for existing rows (assigned to `default`).

## Test plan

- Isolation test: tenant A cannot read tenant B's turns (both SQLite + Postgres).
- Per-tenant settings override resolves correctly at dispatch.
- Disabled path proves identical behaviour to current single-tenant flow.

## Acceptance criteria

- [x] Cross-tenant read isolation proven for SQLite (unit) + Postgres (gated).
- [x] Disabled/absent tenant → single implicit `"default"` tenant, byte-identical.
- [x] Off by default; coverage floors maintained; no protocol break.
- [ ] _(Phase 2)_ `AgentSettings` resolve per tenant with a documented precedence.

## Resolved decisions (ADR-0017)

- **Storage isolation = row filter** (`tenant` column + `WHERE tenant = ?`),
  not schema-per-tenant — simplest, backend-symmetric, and preserves the
  Protocol signature. Existing rows migrate to `"default"` via the column default.
- **Request-path only** for Phase 1 — tenancy does **not** compose with the eval
  harness targets (which run out-of-band); that stays a Phase 2 question.
