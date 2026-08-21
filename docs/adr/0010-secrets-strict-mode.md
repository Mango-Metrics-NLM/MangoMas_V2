# ADR-0010: Secrets strict mode (amends ADR-002)

## Status

Accepted (amends ADR-002)

## Context

[ADR-002](0002-secrets-provider-error-semantics.md) collapses every cloud
secrets failure to `None` so local dev with `MANGOMAS_SECRETS__PROVIDER=gcp`
but no ADC falls back to the inline `api_key`. In production this is a footgun:
a genuine auth/permission/timeout failure is silently masked by the fallback,
and operators only find out via an ERROR log they may not alert on.
`NEXT_STEPS.md` calls for an opt-in "fail loud" mode.

## Decision

Add `SecretsSettings.strict: bool = False`. When `True`, cloud secrets backends
raise a new `SecretsResolutionError` (HTTP 503) on **auth/permission/timeout/API**
failures instead of returning `None`. `NotFound` still returns `None` — an
absent secret is not a failure. The default (`False`) preserves ADR-002 exactly.

## Consequences

### Positive

- Production can fail loud on secret-resolution failures instead of silently
  falling back to an inline key.
- Additive: default-off keeps the local-dev contract byte-identical.
- The error carries only the short secret id + provider + exception class name
  — never the value, resource path, or version.

### Negative / Trade-offs

- Two code paths (raise vs. return `None`) in the backend; contained in one
  `_raise_if_strict` helper.

### Neutral

- `SecretsProvider.get -> str | None` is unchanged; raising is protocol-compatible.
- Only the GCP backend has failure modes today; the env backend never fails.

## Alternatives Considered

- **Always fail loud (drop the None fallback)** — rejected: breaks the ADR-002
  local-dev ergonomics that motivated it.
- **Raise on NotFound too** — rejected: an absent secret with an inline fallback
  is a legitimate configuration, not a failure.

## References

- Code: `src/mangomas/secrets/gcp.py` (`_raise_if_strict`),
  `src/mangomas/errors.py` (`SecretsResolutionError`),
  `src/mangomas/api/errors.py` (`_ERROR_STATUS` → 503),
  `src/mangomas/config/secrets.py` (`SecretsSettings.strict`)
- Spec: `specs/0003-secrets-strict-mode.md`
- Related ADRs: ADR-002 (amended), ADR-0001 (cloud targets)
