---
name: mango-error
description: >
  Adding or modifying a typed error in Mango-Mas V2. Use when: introducing
  a new MangomasError subclass, mapping it to an HTTP status, ensuring the
  existing _ERROR_STATUS contract holds, or updating CHANGELOG for an
  error-surface change. Covers the three-file lock-step (errors.py +
  api/errors.py::_ERROR_STATUS + tests/test_errors.py) that every new error
  must respect.
argument-hint: "Describe the failure mode to model (e.g. 'rate limit exceeded') or paste the traceback"
---

# Mango-Mas Error Skill

## When to Use

- Add a new domain error (e.g. `RateLimitExceeded`, `BudgetExceeded`)
- Promote a bare `RuntimeError` to a typed `MangomasError` subclass
- Re-map an existing error to a different HTTP status
- Diagnose why a particular error returns `500` instead of a specific code
- Add a JSON envelope field to `MangomasError`

---

## Quick Commands

```powershell
# Verify the error → status table is exhaustive
python -m pytest tests/test_errors.py tests/test_api.py -v

# Full suite (errors.py has a 100% floor)
python -m pytest --tb=short -q
```

```bash
python -m pytest tests/test_errors.py tests/test_api.py -v
python -m pytest --tb=short -q
```

---

## Error Rules

| Rule | Detail |
|------|--------|
| Single root | All errors subclass `MangomasError` (never bare `Exception`). |
| `code` attribute | Each subclass sets a unique `code` string. Used in JSON error envelopes. |
| HTTP mapping centralised | The `_ERROR_STATUS` dict in `api/errors.py` is the ONLY place errors map to HTTP status. |
| Walk-the-MRO resolution | `_error_status()` walks the exception's MRO, so subclasses inherit their parent's mapping unless overridden. |
| 100% coverage floor | `errors.py` is at 100%. Every constructor branch must be tested. |
| No leaky messages | Exception messages are safe to surface to clients; never embed internal paths, SQL, or secrets. |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/errors.py` | The full error hierarchy + `__all__` export list |
| `src/mangomas/api/errors.py` | `_ERROR_STATUS` mapping and the `_error_status()` walker |
| `tests/test_errors.py` | Constructor + code-string + MRO-resolution tests |
| `tests/test_api.py` | HTTP integration tests for the mapping |
| `CHANGELOG.md` | Where every new error type must be announced |

---

## Current Hierarchy (from errors.py)

```
MangomasError                          → 500
├── ConfigError                        → 400
│   └── UnknownProvider                → 400
├── AgentNotFound                      → 404
├── LLMError                           → 502
│   ├── LLMTimeout                     → 504
│   ├── LLMUnavailable                 → 503
│   └── LLMBadResponse                 → 502
├── ToolExecutionError                 → 502
├── ToolNotFound                       → 400
├── MaxStepsExceeded                   → 422
├── SecretsResolutionError             → 503   (strict mode only; ADR-0010)
└── PersistenceError                   → 500
```

One more error is mapped in `_ERROR_STATUS` but is **not** in `errors.py`:
`AuthenticationError` → 401, defined in `api/auth.py` because it is a purely
API-layer concern (ADR-0014). It is still a `MangomasError` subclass and still
obeys the same mapping rule — the three-file lock-step below applies to it too,
with `api/auth.py` standing in for `errors.py`.

---

## Template — Add a New Error

```python
# src/mangomas/errors.py
class RateLimitExceeded(LLMError):
    """Raised when the upstream LLM rejects a request for rate-limiting."""

    code = "rate_limit_exceeded"

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(
            f"Rate limit exceeded; retry after {retry_after_seconds:.1f}s.",
        )
        self.retry_after_seconds = retry_after_seconds
```

Add to `__all__` at the top of `errors.py`. Add to `_ERROR_STATUS` in `api/errors.py`:

```python
_ERROR_STATUS: dict[type[MangomasError], int] = {
    # ...existing entries...
    RateLimitExceeded: HTTPStatus.TOO_MANY_REQUESTS,
}
```

Add a test in `tests/test_errors.py`:

```python
def test_rate_limit_exceeded_carries_retry_after() -> None:
    exc = RateLimitExceeded(retry_after_seconds=5.0)
    assert exc.code == "rate_limit_exceeded"
    assert exc.retry_after_seconds == 5.0
    assert isinstance(exc, MangomasError)
```

Add a test in `tests/test_api.py` asserting the HTTP code via the orchestrator → endpoint path (use `FakeLLM(raises=RateLimitExceeded(5.0))` or extend `FakeLLM` accordingly).

Append a CHANGELOG entry under `### Added`.

---

## Workflow

1. Read `src/mangomas/errors.py` to find the most specific existing parent.
2. Subclass it (or `MangomasError` if no parent fits).
3. Set `code` and any structured fields in `__init__`.
4. Add to `__all__` at the top of `errors.py`.
5. Add the HTTP mapping in `api/errors.py::_ERROR_STATUS`.
6. Write a constructor test in `tests/test_errors.py`.
7. Write an HTTP-mapping test in `tests/test_api.py`.
8. Update `CHANGELOG.md`.
9. Run `ruff check --fix`, `mypy --strict`, `pytest --tb=short -q`.

---

## Constraints

- DO NOT raise bare `Exception` or `RuntimeError` in `src/` — always use a `MangomasError` subclass.
- DO NOT put HTTP status decisions outside `api/errors.py::_ERROR_STATUS`.
- DO NOT skip the test in `tests/test_errors.py` — the 100% coverage floor will fail CI.
- DO NOT embed secrets, file paths, or SQL in the exception message — they are surfaced to clients.

---

## Diagnosing Failures

1. New error returns `500` instead of expected code → not yet added to `_ERROR_STATUS`.
2. Coverage gate fails for `errors.py` → constructor branch (e.g. optional kwarg) not tested.
3. Existing test breaks after re-mapping → check `tests/test_api.py` for hardcoded status numbers; replace with `tests.constants` references.
4. mypy complains about `Type[MangomasError]` in `_ERROR_STATUS` → ensure the new class is exported in `__all__` and imported in `api/errors.py`.
