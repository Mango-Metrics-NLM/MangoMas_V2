# ADR-0014: Application authentication seam

## Status

Accepted

## Context

The FastAPI surface has no inbound authentication; only Cloud Run IAM guards it,
which gives no per-caller identity and nothing for gateway / off-GCP deployments.
We need an opt-in, default-OFF API auth check that reuses the shipped
`SecretsProvider` seam, keeps the response envelope consistent, does not edit
protected `errors.py`, and is the trust boundary multi-tenancy will build on. The
open question is where the 401 error type lives, given `errors.py` is protected.

## Decision

Add a default-OFF `AuthSettings` (`enabled`, `secret_ref`) and a FastAPI
dependency (`api/auth.py::require_auth`) mounted on the data + execution routes.
The expected token is resolved once at app build from `secret_ref` via the
configured `SecretsProvider`, stored on `app.state.auth`, and compared in constant
time against `Authorization: Bearer` / `X-API-Key`. The 401 error type
(`AuthenticationError`) is a `MangomasError` subclass defined in the **api layer**
and mapped in `api/app.py::_ERROR_STATUS` — `errors.py` stays untouched.

## Consequences

### Positive

- Per-caller auth on the served surface, reusing the `SecretsProvider` seam;
  fail-closed when the secret does not resolve; consistent JSON error envelope
  (the existing `MangomasError` handler renders it).
- Zero protected-path edit; the prerequisite trust boundary for multi-tenancy.

### Negative / Trade-offs

- `AuthenticationError` lives in `api/auth.py`, not `errors.py`, so the typed-error
  hierarchy is no longer wholly centralized. This is a deliberate trade to avoid a
  protected-path edit for an inherently api-layer concern (auth exists only at the
  HTTP surface); the status mapping still lives in the one `_ERROR_STATUS` table.

### Neutral

- Probes (`/healthz`, `/readyz` + aliases) and `GET /agents` stay unauthenticated
  so Cloud Run health checks and discovery keep working.
- `AuthSettings` requires `secret_ref` when enabled (a validator, mirroring
  `WorkflowSettings.definition`). The `env` provider is the v1 path; gcp-backed
  auth requires the gcp secrets provider registered (fail-closed otherwise).

## Alternatives Considered

- **Add `AuthenticationError` to `errors.py`** — rejected for now: it is the
  idiomatic home but a protected-path edit for an api-only concern; deferred to an
  `error-taxonomy-dev` lock-step if auth errors ever need to arise below the api.
- **FastAPI `HTTPException(401)`** — rejected: produces FastAPI's `{"detail": …}`
  envelope, inconsistent with the app's `{"error", "message"}` envelope.
- **Middleware instead of a dependency** — rejected: a route dependency composes
  with FastAPI's per-route mounting and OpenAPI, and is trivially exempted on probes.

## References

- Code: `src/mangomas/api/auth.py`, `src/mangomas/api/app.py` (`_ERROR_STATUS`,
  route `dependencies`), `src/mangomas/config.py::AuthSettings`,
  `src/mangomas/secrets/` (`SecretsProvider`, `secrets_registry`).
- Related: ADR-0002 (secrets error semantics); spec `specs/0010-application-auth-seam.md`.
