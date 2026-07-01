# Spec-0007: Multi-tenancy

- **Status:** Draft (stub — no code this pass)
- **Linked ADR:** ADR-0009 (to be authored when implementation begins)
- **Linked CHANGELOG entry:** _pending_

## Problem

`NEXT_STEPS.md` › "Long term" › *Multi-tenancy*: a single deployment should
serve multiple tenants with **tenant-scoped conversation storage** and
**per-tenant agent configuration**, without leaking state across tenants.

## Requirements

- A tenant identifier flows through the request path (proposed:
  `X-Tenant-ID` header → correlation-style `ContextVar`, reusing the
  `correlation.py` pattern).
- `TurnRepository` reads/writes are scoped by tenant (schema/prefix/row-filter —
  decide per backend in the ADR); no cross-tenant reads possible.
- Per-tenant `AgentSettings` via a tenant-keyed registry, resolved at dispatch.
- Must remain **additive & default-OFF**: absent tenant id → a single implicit
  "default" tenant, behaviour byte-identical to today.

## Config / env additions (sketch)

| Env var | Default | Purpose |
|---------|---------|---------|
| `MANGOMAS_TENANCY__ENABLED` | `false` | Enable tenant scoping |
| `MANGOMAS_TENANCY__HEADER` | `X-Tenant-ID` | Inbound tenant header |
| `MANGOMAS_TENANCY__DEFAULT` | `default` | Implicit tenant when none supplied |

## Protocol / contract impact

- `TurnRepository` gains a tenant-scoping seam **without breaking** the existing
  signature (candidate: an optional `tenant` kwarg defaulting to the implicit
  tenant, or a scoped-repository factory — evaluate both in ADR-0009 to keep the
  protocol backwards-compatible).
- New `TenancySettings` group; new middleware analogous to `AccessLogMiddleware`.

## Backwards-compatibility

- Disabled → one implicit tenant; existing single-tenant deployments unaffected.
- Storage migration path documented for existing rows (assigned to `default`).

## Test plan

- Isolation test: tenant A cannot read tenant B's turns (both SQLite + Postgres).
- Per-tenant settings override resolves correctly at dispatch.
- Disabled path proves identical behaviour to current single-tenant flow.

## Acceptance criteria

- [ ] Cross-tenant read isolation proven for every `TurnRepository` impl.
- [ ] `AgentSettings` resolve per tenant with a documented precedence.
- [ ] Off by default; 95% coverage maintained; no protocol break.

## Open questions

- Storage isolation strategy per backend (schema-per-tenant vs. row filter)?
- Does tenancy compose with the eval harness targets, or stay request-path only?
