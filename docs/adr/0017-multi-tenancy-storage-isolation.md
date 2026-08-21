# ADR-0017: Multi-tenancy storage isolation (Phase 1)

## Status

Accepted

## Context

Multi-tenancy (spec 0007) requires tenant-scoped conversation storage so a single
deployment can serve multiple tenants without leaking turns across them, while
staying additive and default-OFF and **not** breaking the `TurnRepository`
Protocol. Two decisions are open: (1) the storage isolation strategy, and (2) how
the tenant reaches the repository without a signature change. (The stub spec
mislabelled the ADR as "ADR-0009", which is already the telemetry-exporter seam —
this is the corrected, next-free number.)

## Decision

**Row-filter isolation, tenant carried by a `ContextVar`.** A `tenant` column
(`TEXT NOT NULL DEFAULT 'default'`) is added to the `turns` table in both the
SQLite and Postgres repositories; `list_turns` filters `WHERE tenant = ?` and
`save_turn` stamps the active tenant. The tenant is resolved from a
`tenancy.tenant_id` `ContextVar` (a clone of `correlation.py`) set by a
`TenancyMiddleware` from the `X-Tenant-ID` header — read **inside** each repo
method (in the async context, then captured into the `asyncio.to_thread`
closure), so the Protocol signature is byte-identical. `get_tenant()` falls back
to `DEFAULT_TENANT = "default"`, so a disabled deployment stamps and filters
everything as `"default"` — byte-identical to today. Per-tenant `AgentSettings`
(Phase 2) is deferred.

## Consequences

### Positive

- No `TurnRepository` signature change (tenant via `ContextVar`, like
  `correlation_id`); no protected-path edit; disabled path byte-identical.
- Backend-symmetric (one `tenant` column + `WHERE` clause in each repo); existing
  rows migrate to `"default"` via the column default (idempotent `ADD COLUMN`).

### Negative / Trade-offs

- Row-filter isolation shares one table/schema across tenants — a query bug could
  in principle cross tenants. Mitigated by routing every read through the single
  `WHERE tenant = ?` path and an isolation test per backend. Schema-per-tenant
  would be stronger but breaks the Protocol symmetry and adds migration weight.
- The tenant must be read in the async method and passed into the worker-thread
  closure — reading the `ContextVar` inside the thread is **not** relied upon.

### Neutral

- Request-path only: tenancy does not scope the eval harness (out-of-band) in v1.
- `TenancySettings` (`MANGOMAS_TENANCY__*`) default-OFF; `tenancy.py` imports only
  stdlib, so `config.py` can import `DEFAULT_TENANT` without a cycle.

## Alternatives Considered

- **Optional `tenant` kwarg on `TurnRepository`** — rejected: changes the
  protected-ish adapter contract and every call site; the `ContextVar` keeps the
  signature untouched.
- **Schema-per-tenant / DB-per-tenant** — rejected for Phase 1: stronger isolation
  but asymmetric across backends, heavier migration, and provisioning per tenant.
- **Reusing `correlation_id`** — rejected: correlation is per-request and
  operator-facing; tenancy is a distinct, coarser scope needing its own ContextVar.

## References

- Code: `src/mangomas/tenancy.py`, `src/mangomas/api/middleware.py`
  (`TenancyMiddleware`), `src/mangomas/config/api.py::TenancySettings`,
  `src/mangomas/adapters/storage/{sqlite.py,postgres.py}`. Pattern cloned from
  `src/mangomas/correlation.py`.
- Related: spec `specs/0007-multi-tenancy.md`; ADR-0002 (secrets error semantics,
  the ContextVar-seam precedent).
