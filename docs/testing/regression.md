# Regression Baseline

This document describes the regression test suite for Mango-Mas V2 and the
coverage floors that must pass before any merge.

---

## Test suite

The existing test suite (`tests/`) constitutes the regression baseline.
It covers unit, API, and integration boundaries with all external services
replaced by deterministic test doubles (`FakeLLM`, `FakeRepository`,
`FakeMemoryRepository`, `FakeTool`).

### Current baseline (as of v0.1.0)

| Metric | Value |
|---|---|
| Tests collected | 234 |
| Tests passed | 233 |
| Tests skipped | 1 (integration — requires `RUN_INTEGRATION=1`) |
| Tests skipped | 1 (LM Studio — requires `RUN_LMSTUDIO=1`) |
| Global coverage | 96.86% |

### Running the baseline

```powershell
# From the repo root with the venv activated
python -m pytest -q
```

Expected output: `233 passed, 1 skipped` (integration skipped by default).

---

## Per-package coverage floors

Enforced by `scripts/check_coverage.py` in CI and locally:

| Package | Floor |
|---|---|
| `errors` | 100% |
| `registry` | 100% |
| `core` | 100% |
| `composition` | 90% |
| `agents` | 95% |
| `api` | 90% |
| `cli` | 90% |
| `adapters` | 85% |
| Global | 90% |

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

## LM Studio smoke test

`tests/lmstudio/test_smoke.py` (`test_lmstudio_ping`) tests connectivity to a
running LM Studio server.  It is skipped by default and does **not** affect
coverage floors when skipped.

```powershell
$env:RUN_LMSTUDIO = '1'
$env:LMSTUDIO_MODEL = 'google/gemma-4-e4b'   # or your loaded model
python -m pytest tests/lmstudio --no-cov -q
```

For remaining LM Studio scenarios (chat, streaming, summarize, error path)
see [lmstudio-e2e.md](lmstudio-e2e.md).

---

## Full gate (matches CI)

```powershell
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy --strict src tests scripts
python -m pytest -q
python scripts/check_coverage.py
```
