# Regression Baseline

This document describes the regression test suite for Mango-Mas V2 and the
coverage floors that must pass before any merge.

---

## Test suite

The existing test suite (`tests/`) constitutes the regression baseline.
It covers unit, API, and integration boundaries with all external services
replaced by deterministic test doubles (`FakeLLM`, `FakeRepository`,
`FakeMemoryRepository`, `FakeTool`).

### Current baseline (as of v0.3.1)

| Metric | Value |
|---|---|
| Tests collected | 533 |
| Tests passed | 515 |
| Tests skipped | 18 (integration/LM Studio/Vertex/Postgres — gated) |
| Global coverage | 98.16% |
| Coverage floor | 95% (enforced by `pyproject.toml --cov-fail-under`) |
| All 11 coverage floors | ✅ Met |
| Mypy (strict, 127 files) | 0 errors |
| Ruff lint + format | Clean |

### Running the baseline

```powershell
# From the repo root with the venv activated
python -m pytest -q
```

Expected output: `515 passed, 18 skipped`.

---

## Per-package coverage floors

Enforced by `scripts/check_coverage.py` in CI and locally:

| Package | Floor | Actual (v0.3.1) |
|---|---|---|
| `errors` | 100% | 100% |
| `registry` | 100% | 100% |
| `core` | 100% | 100% |
| `secrets` | 100% | 100% |
| `correlation` | 100% | 100% |
| `composition` | 95% | 100% |
| `agents` | 95% | 97% |
| `api` | 95% | 94–98% |
| `cli` | 95% | 96% |
| `eval` | 95% | 100% |
| `adapters` | 85% | varies (80–100% per module) |
| **Global** | **95%** | **98.16%** |

```powershell
python scripts/check_coverage.py
```

Floors are enforced in CI; a PR that drops any floor will fail the
`Per-package coverage floors` step.

---

## Integration test

`tests/integration/test_api_flow.py` uses `httpx.ASGITransport` to exercise
the full FastAPI app in-process without requiring any external service.

```powershell
$env:RUN_INTEGRATION = '1'
python -m pytest tests/integration --no-cov -q
```

---

## LM Studio E2E (7 scenarios)

Gated on `RUN_LMSTUDIO=1`. All 7 scenarios pass as of v0.3.1:

| Scenario | Test file | Status |
|---|---|---|
| Ping (smoke) | `tests/lmstudio/test_smoke.py` | ✅ |
| Chat invoke | `tests/lmstudio/test_chat_invoke.py` | ✅ |
| Chat stream SSE | `tests/lmstudio/test_chat_stream.py` | ✅ |
| Stream fallback | `tests/lmstudio/test_stream_fallback.py` | ✅ |
| Summarize invoke | `tests/lmstudio/test_summarize_invoke.py` | ✅ |
| Unknown model 502 | `tests/lmstudio/test_unknown_model.py` | ✅ |
| API flow (integration) | `tests/integration/test_api_flow.py` | ✅ |

```powershell
$env:RUN_LMSTUDIO = '1'
$env:LMSTUDIO_MODEL = 'google/gemma-4-e4b'   # or your loaded model
python -m pytest tests/lmstudio --no-cov -q
```

---

## GCP E2E tests

### GCP Secret Manager

Gated on `RUN_INTEGRATION=1`. Tests live secret resolution via ADC.

```powershell
$env:RUN_INTEGRATION = '1'
python -m pytest tests/integration/test_gcp_secrets_live.py --no-cov -q
```

Status: ✅ PASS — resolves `MANGOMAS_LLM_API_KEY` secret from project `hrmtrain`.

### Vertex AI

Gated on `RUN_VERTEX=1`. Requires Gemini model access in the GCP project.

```powershell
$env:RUN_VERTEX = '1'
$env:VERTEX_PROJECT_ID = 'your-gcp-project'
$env:VERTEX_LOCATION = 'us-central1'
$env:VERTEX_MODEL = 'gemini-1.5-flash'
python -m pytest tests/vertex --no-cov -q
```

Status: ⚠️ BLOCKED — project `hrmtrain` returns 404 for all Gemini models.
Error translation code is correct (unit tests pass); issue is project-level
model access in GCP Console.

### Postgres (testcontainers)

Gated on `RUN_POSTGRES=1`. Requires Docker for testcontainers.

```powershell
$env:RUN_POSTGRES = '1'
python -m pytest tests/postgres --no-cov -q
```

---

## Full gate (matches CI)

```powershell
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy --strict src tests scripts
python -m pytest -q
python scripts/check_coverage.py
```
