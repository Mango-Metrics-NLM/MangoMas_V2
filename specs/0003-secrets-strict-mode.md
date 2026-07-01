# Spec-0003: SecretsSettings.strict + SecretsResolutionError

- **Status:** Implemented (Milestone D)
- **Linked ADR:** ADR-0010 (amends ADR-002)
- **Linked CHANGELOG entry:** `[Unreleased]` › `Added — Secrets strict mode`

## Problem

`NEXT_STEPS.md` › "SecretsSettings.strict mode (ADR-002 follow-up)": give
operators an opt-in "fail loud" mode where cloud secret-resolution failures
raise instead of collapsing to `None` (which masks misconfiguration in prod).

## Requirements

- `MANGOMAS_SECRETS__STRICT` (default `False`) toggles the behaviour.
- When `True`, auth/permission/timeout/API failures raise
  `SecretsResolutionError`; `NotFound` still returns `None`.
- The raised error carries no sensitive context (short id + provider only).

## Config / env additions

| Env var | Default | Purpose |
|---------|---------|---------|
| `MANGOMAS_SECRETS__STRICT` | `false` | Raise on cloud secret failures instead of returning `None` |

`DEFAULT_SECRETS_STRICT` in `config.py`; new `SecretsSettings.strict` field.

## Protocol / contract impact

- New `SecretsResolutionError(MangomasError)` (`code="secrets_resolution_error"`),
  mapped to HTTP 503 in `api/app.py::_ERROR_STATUS`. `SecretsProvider.get`
  signature unchanged (raising is protocol-compatible).

## Backwards-compatibility

- Default `strict=False` → ADR-002 "collapse to None" behaviour, byte-identical.

## Test plan

- `tests/test_errors.py`: code + `all_errors` membership + attr storage.
- `tests/test_api.py`: `SecretsResolutionError → 503`.
- `tests/test_secrets_gcp.py`: strict raises on each failure mode; NotFound still
  returns `None`; strict propagates through `_resolve_llm_secrets`.

## Acceptance criteria

- [x] Default off → ADR-002 behaviour unchanged.
- [x] Strict raises on auth/permission/timeout/API; NotFound → `None`.
- [x] Error → 503; no sensitive data in the error. 95% coverage maintained.
