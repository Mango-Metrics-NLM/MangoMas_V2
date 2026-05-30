# Regression Baseline

This document describes the regression test suite for Mango-Mas V2 and the
coverage floors that must pass before any merge.

---

## Test suite

The existing test suite (`tests/`) constitutes the regression baseline.
It covers unit, API, and integration boundaries with all external services
replaced by deterministic test doubles (`FakeLLM`, `FakeRepository`,
`FakeMemoryRepository`, `FakeTool`, `FakeEmbeddingClient`, `FakeVectorStore`).

### Current baseline (RAG branch — Unreleased)

| Metric | Value |
|---|---|
| Tests collected | 658 |
| Tests passed | 639 |
| Tests skipped | 19 (integration/LM Studio/Vertex/Postgres/RAG-local — gated) |
| Global coverage | 97.95% |
| Coverage floor | 95% (enforced by `pyproject.toml --cov-fail-under`) |
| All coverage floors | ✅ Met (incl. new `rag` 95% floor) |
| Mypy (strict) | 0 errors |
| Ruff lint + format | Clean |
| Frontmatter lint (`lint_agent_frontmatter.py`) | Clean |

### Running the baseline

```powershell
# From the repo root with the venv activated
python -m pytest -q
```

Expected output: `639 passed, 19 skipped`.

### New suites on the RAG branch

| Suite | Path | Covers |
|---|---|---|
| Embedding adapters | `tests/adapters/embeddings/` | LM Studio (respx), sentence-transformers + Vertex (injected fakes) |
| Shared error helpers | `tests/adapters/test_shared_errors.py` | `_http_errors` / `_vertex_errors` translation + detail truncation |
| Vector adapter | `tests/adapters/vector/` | `ChromaVectorStore` via injected fake collection; cosine scoring |
| RAG domain | `tests/rag/` | chunker (+ Hypothesis fuzz), models, loader, pipeline, retrieval, ToolAgent-invokes-RetrievalTool, gated end-to-end |
| CLI RAG | `tests/test_cli_rag.py` | `mangomas rag ingest|query` (Typer `CliRunner`, fakes injected) |
| Orchestrator teardown | `tests/test_orchestrator_aclose.py` | `aclose()` closes embeddings + vector store, fault-tolerant + idempotent |

The previous v0.3.1 baseline was **533 collected / 515 passed / 18 skipped /
98.16%**. The RAG port adds the opt-in embeddings + vector + `rag/` layers
(all `enabled=False` by default), so no prior behaviour changed.

---

## Per-package coverage floors

Enforced by `scripts/check_coverage.py` in CI and locally:

| Package | Floor | Status |
|---|---|---|
| `errors` | 100% | ✅ Met |
| `registry` | 100% | ✅ Met |
| `core` | 100% | ✅ Met |
| `secrets` | 100% | ✅ Met |
| `correlation` | 100% | ✅ Met |
| `composition` | 95% | ✅ Met |
| `agents` | 95% | ✅ Met |
| `api` | 95% | ✅ Met |
| `cli` | 95% | ✅ Met |
| `eval` | 95% | ✅ Met |
| `rag` | 95% | ✅ Met |
| `adapters` | 85% | ✅ Met (varies per module; embeddings/vector adapters covered via injected fakes, lazy SDK paths `# pragma: no cover`) |
| **Global** | **95%** | **97.95%** |

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

## RAG local end-to-end (opt-in)

Gated on `RUN_EMBEDDINGS_LOCAL=1` (and `RUN_RAG=1` for the Chroma path).
Exercises ingest → query through real sentence-transformers + an ephemeral
Chroma store; no server required.

```powershell
pip install -e ".[dev,embeddings-local,rag]"
$env:RUN_EMBEDDINGS_LOCAL = '1'
$env:RUN_RAG = '1'
python -m pytest tests/rag -q
```

---

## Full gate (matches CI)

```powershell
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m mypy --strict src tests scripts
python -m pytest -q
python scripts/check_coverage.py
python scripts/lint_agent_frontmatter.py
```
