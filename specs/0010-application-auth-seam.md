# Spec-0010: Application authentication seam

- **Status:** Implemented
- **Linked ADR:** ADR-0014
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added`

## Problem

The HTTP surface has no inbound authentication — only Cloud Run IAM (a coarse
network-perimeter check with no per-caller identity) guards the endpoints, and
nothing protects a deployment behind a gateway or off-GCP. This spec adds an
opt-in bearer / API-key check on the data + execution routes, resolving the
expected token via the existing `SecretsProvider` seam. It is also the
prerequisite for multi-tenancy (tenant identity is only trustworthy once callers
are authenticated).

## Requirements

- A FastAPI dependency guards `/agents/{name}/invoke`, `/agents/{name}/stream`,
  `/history`, and `/workflows/*`. Health/readiness probes (`/healthz`, `/readyz`
  + aliases) and `GET /agents` stay open (Cloud Run needs unauthenticated probes).
- The client presents the token as `Authorization: Bearer <token>` **or**
  `X-API-Key: <token>`; comparison is constant-time (`secrets.compare_digest`).
- The expected token is resolved once (at app build) from `AuthSettings.secret_ref`
  via the configured `SecretsProvider`; **fail-closed** — if auth is enabled but
  the token does not resolve, every guarded request is rejected.
- Must remain **additive & default-OFF**: `auth.enabled=false` (default) → the
  dependency is a no-op pass-through, behaviour byte-identical.

## Config / env additions

| Env var (`MANGOMAS_*`) | Default | Purpose |
|------------------------|---------|---------|
| `MANGOMAS_AUTH__ENABLED` | `false` | Enforce API auth on guarded routes |
| `MANGOMAS_AUTH__SECRET_REF` | _(none)_ | `SecretsProvider` reference resolving to the expected token |

`AuthSettings` requires `secret_ref` when `enabled` (a model validator, mirroring
`WorkflowSettings`). New `DEFAULT_AUTH_*` constants.

## Protocol / contract impact

- New/changed protocols: _none_ (reuses `SecretsProvider`).
- New error types: `AuthenticationError` (code `authentication_error`, HTTP 401),
  defined in the **api layer** (`api/auth.py`) as a `MangomasError` subclass and
  mapped in `api/app.py::_ERROR_STATUS` — `errors.py` (protected) is untouched.
- Registry additions: _none_ (reuses `secrets_registry`).

## Backwards-compatibility

- Disabled by default → dependency no-op; every route byte-identical.
- No protected-path edit; `create_app` signature unchanged (auth state stored on
  `app.state.auth`).

## Test plan

- Unit (`tests/test_auth.py`, api floor 95): disabled → open; enabled + correct
  `Bearer` / `X-API-Key` → 200; enabled + missing/wrong → 401; probes open
  regardless; fail-closed when the secret does not resolve. Env-driven token via
  the `env` `SecretsProvider`.
- Header/token constants in `tests/constants.py`.
- Coverage: maintain the 95% global gate and the api floor.

## Acceptance criteria

- [x] Disabled → guarded routes open (test proves it).
- [x] Enabled → correct token (Bearer or X-API-Key) 200; missing/wrong 401;
      probes open; unresolved secret → fail-closed 401.
- [x] `ruff`, `mypy`, `pytest` (95% gate), `frontmatter-lint` all clean.
- [x] CHANGELOG updated; ADR-0014 added.
