# ADR-002: Cloud SecretsProvider Error Semantics

## Status

Accepted.

## Context

The `SecretsProvider` protocol (`src/mangomas/secrets/provider.py`)
shipped in v0.2.0 with a single contract: `get(name) -> str | None`,
returning `None` to indicate "the reference is not configured". With the
env-var backend (`EnvSecretsProvider`) this meant exactly one thing —
the environment variable was unset.

v0.3.0 introduces a Google Secret Manager backend
(`GCPSecretManagerProvider`). Cloud lookups can fail for many reasons
that have no env-var analogue:

- `google.api_core.exceptions.NotFound` — secret does not exist.
- `google.api_core.exceptions.PermissionDenied` /
  `google.api_core.exceptions.Unauthenticated` — IAM misconfiguration.
- `google.auth.exceptions.DefaultCredentialsError` — Application Default
  Credentials are not present at all.
- `google.api_core.exceptions.DeadlineExceeded` — network or service
  slowness.
- Any other `google.api_core.exceptions.GoogleAPIError`.

The question: should the cloud provider raise on these errors, or
collapse them into `None` and let the composition layer fall back to the
inline `api_key` setting at `composition.py:_resolve_llm_secrets`?

## Decision

**Collapse all failure modes into `None`** and emit an ERROR-level
structured log record (NotFound emits DEBUG only since it matches the
env-var "missing" semantics).

The log record carries `extra={"error": type(exc).__name__,
"project_id": ..., "secret_name": <short id only>}`. Operators MUST
alert on `logger=mangomas.secrets.gcp severity=ERROR` to detect rotation
failures, IAM regressions, and outages.

## Consequences

### Positive

- The protocol contract (`get -> str | None`) is preserved unchanged;
  the env backend and the cloud backend are interchangeable from the
  composition layer's perspective (`composition.py:67-73`).
- Local development with `MANGOMAS_SECRETS__PROVIDER=gcp` set but no
  ADC configured does not crash on startup — the inline `api_key`
  fallback continues to work, exactly as it does with the env backend.
- Existing tests for `_resolve_llm_secrets` cover the fallback path; no
  new code path is required in composition.

### Negative

- A rotated secret can silently degrade to a stale inline `api_key`,
  masking what is effectively a production incident. The only signal is
  the ERROR log line. Operators MUST grep / alert on this signal.
- Distinguishing "secret genuinely missing" from "auth genuinely broken"
  requires reading log records — there is no programmatic discriminator
  on the call site.

### Alternatives considered

1. **Raise on auth failures.** Rejected: breaks local-dev startup when
   GCP isn't reachable and an inline `api_key` is set.
2. **Return a sum type** (`SecretResult = Found(str) | Missing |
   Error(str)`). Rejected: breaks the existing protocol; cascades into
   every caller of `secrets_registry.get(name).get(ref)`.
3. **Two protocol methods** (`get_or_raise` + `get`). Rejected: same
   cascade as (2) plus protocol bloat.

## Follow-up (deferred to v0.4.0)

Add `SecretsSettings.strict: bool = False`; when set, raise a new
`SecretsResolutionError` from the cloud backends (not env) instead of
returning `None`. This gives operators an opt-in "fail loud" mode for
production while preserving the local-dev contract by default. Tracked
under NEXT_STEPS.md mid-term alongside the Cloud Logging exporter.
