# Regression Baseline

This document describes the regression test suite for Mango-Mas V2 and the
coverage floors that must pass before any merge.

---

## Test suite

The existing test suite (`tests/`) constitutes the regression baseline.
It covers unit, API, and integration boundaries with all external services
replaced by deterministic test doubles (`FakeLLM`, `FakeRepository`,
`FakeMemoryRepository`, `FakeTool`, `FakeEmbeddingClient`, `FakeVectorStore`,
`FakeCognitiveSink`).

### Current baseline

Absolute test counts are deliberately **not** recorded here — they went stale
within a release every time they were. The suite's health is defined by the
gates below, all of which are executable:

| Gate | Command | Authority |
|---|---|---|
| Unit suite + global floor | `make test` | `pyproject.toml` addopts mirror the global floor |
| Per-package floors | `make coverage` | **`scripts/check_coverage.py` — the authoritative gate** |
| Bridge floor (100%) | `make bridge-coverage` | `eval_harness_bridge` is gated separately |
| Contracts floor (100%) | `make contracts-coverage` | `mango-integration-contracts` is gated separately |
| Mypy (strict) | `make typecheck` | 0 errors required |
| Ruff lint + format | `make lint` / `make format-check` | Clean required |
| Frontmatter lint | `make frontmatter` | `scripts/lint_agent_frontmatter.py` |
| Everything above | `make gate` | Mirrors `.github/workflows/ci.yml` |

`pyproject.toml`'s `--cov-fail-under` **mirrors** the global floor for local
runs; `scripts/check_coverage.py` is the single source of truth, because it is
the only place that also enforces the per-package floors.

### Running the baseline

```powershell
# From the repo root with the venv activated
make gate          # or: python -m pytest -q && python scripts/check_coverage.py
```

Skips are expected: every opt-in suite (integration, LM Studio, Vertex,
Postgres, RAG-local, Langfuse) is env-gated and does not run by default — see
the per-suite sections below, or use the matching `make` target
(`make integration`, `make lmstudio`, `make vertex`, `make postgres`,
`make rag`).

### Suite inventory

| Suite | Path | Covers |
|---|---|---|
| Embedding adapters | `tests/adapters/embeddings/` | LM Studio (respx), sentence-transformers + Vertex (injected fakes), lazy-SDK-init wiring |
| Shared error helpers | `tests/adapters/test_shared_errors.py` | `_http_errors` / `_vertex_errors` translation + detail truncation |
| Shared HTTP client base | `tests/adapters/test_openai_client.py` | `OpenAICompatHTTPClient` lifecycle: client ownership, `ClassVar` enforcement, MRO tail, public constructor stability |
| Vector adapter | `tests/adapters/vector/` | `ChromaVectorStore` via injected fake collection; cosine scoring |
| RAG domain | `tests/rag/` | chunker (+ Hypothesis fuzz), models, loader, pipeline, retrieval, ToolAgent-invokes-RetrievalTool; **tier-3 gated device contract** (embedding finiteness/ordering, CPU-vs-auto ranking parity, real-retrieval ToolAgent, CLI round trip) |
| **Tier-1 E2E** | `tests/integration/` | Flows through the **real composition root** (`build_orchestrator` → `create_app`): stream persistence → `/history`, `LoopSettings` → 504 + budget precedence, the shipped `plan-execute-review` graph with validation, `branch`/composite `fan_out` over HTTP, `MODEL_OVERRIDE`, tenant-scoped streamed turns, **CognitiveSignal flag-off/flag-on** (`tests/integration/test_signal_flow.py`). Runs in CI on every push |
| Cognitive producer | `tests/cognitive/` | Flag-off identity, JSONL/HTTP/composite sinks, INV-16 PDP refuse-don't-strip, role map, retrieve-only, GenAI span exporter |
| Gated-suite parity | `tests/deploy/test_gated_suite_homes.py` | Every `RUN_*` suite has an executing home or a recorded infeasibility reason — and never both |
| Hardware contract | `tests/tooling/test_e2e_hardware_contract.py` | Lints tiers 2/3 for elapsed-time assertions, numeric timeouts, device literals, embedding equality |
| Eval serializer | `tests/eval/test_serialize.py` | `report_payload` shape + round trip through `load_baseline` |
| Workflow graph | `tests/test_workflow_*.py` | graph model, predicates, loader, registry (per-kind factory guards), executors, HTTP endpoints, CLI |
| Deploy contract | `tests/deploy/` | `service.yaml` / `deploy.yml` shape; Dockerfile ↔ `.dockerignore` build-context consistency |
| CLI RAG | `tests/test_cli_rag.py` | `mangomas rag ingest|query` (Typer `CliRunner`, fakes injected) |
| Orchestrator teardown | `tests/test_orchestrator_aclose.py` | `aclose()` closes embeddings + vector store, fault-tolerant + idempotent |

---

## Per-package coverage floors

Enforced by `scripts/check_coverage.py` in CI and locally:

| Package | Floor |
|---|---|
| `errors` | 100% |
| `registry` | 100% |
| `core` | 100% |
| `secrets` | 100% |
| `correlation` | 100% |
| `tenancy` | 100% |
| `headers` | 100% |
| `entry_points` | 100% |
| `composition` | 95% |
| `agents` | 95% |
| `api` | 95% |
| `cli` | 95% |
| `config` | 95% |
| `telemetry` | 95% |
| `metrics` | 95% |
| `eval` | 95% |
| `rag` | 95% |
| `workflow` | 95% |
| `cognitive` | 95% |
| `harness` | 95% |
| `adapters` | 85% (varies per module; embeddings/vector adapters covered via injected fakes, lazy SDK paths `# pragma: no cover`) |
| **Global** | **95%** |

Three floors sit outside `FLOORS` because they measure a different tree:
`eval_harness_bridge` (100%, `make bridge-coverage`),
`mango-integration-contracts` (100%, `make contracts-coverage`), and
`scripts/` (92%, `make scripts-coverage`, a measured ratchet rather than a
round number).

`scripts/check_coverage.py` is the authority — if this table and that script
ever disagree, the script wins and this table is the bug.

```powershell
make coverage      # python scripts/check_coverage.py
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

## RAG local end-to-end (opt-in) — the tier-3 device contract

Gated on `RUN_EMBEDDINGS_LOCAL=1` (and `RUN_RAG=1` for the Chroma path).
Exercises ingest → query through real sentence-transformers + an ephemeral
Chroma store; no server required.

**This is the only suite that runs real numerical compute**, so it is the only
one where the torch device matters. It runs nightly on a CPU runner
(`nightly.yml`'s `embeddings-local` job, CPU torch wheels). Its oracles are
rankings and tolerances, never raw scores — two devices reduce float32 sums in
a different order, so identical inputs give scores that differ in the last few
decimals while the ordering does not (spec-0029 R2.2).

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only build
pip install -e ".[dev,embeddings-local,rag]"
$env:RUN_EMBEDDINGS_LOCAL = '1'
$env:RUN_RAG = '1'
python -m pytest tests/rag -q

# On a GPU box, reproduce what the nightly CPU runner sees:
$env:MANGOMAS_EMBEDDINGS__DEVICE = 'cpu'
python -m pytest tests/rag -q
```

`MANGOMAS_EMBEDDINGS__DEVICE` (default unset = the library's auto-detect,
CUDA → MPS → CPU) is both the operator knob — force `cpu` on a box whose GPU is
already serving an LLM — and the way one machine can compare a forced device
against auto-detect. The nightly job deliberately leaves it unset: the runner
has no GPU, so auto-detect resolves to CPU by itself, and pinning it would make
the parity assertion vacuous.

---

## Full gate (matches CI)

```powershell
make gate
```

`make gate` is the whole of `.github/workflows/ci.yml`, in CI's order. The
underlying commands, if you prefer to run them individually — note the lint
surface includes `eval_harness_bridge/src` and
`mango-integration-contracts/src`, and each isolated package carries its own
100% floor as a separate CI job:

```powershell
python -m ruff check src tests scripts eval_harness_bridge/src mango-integration-contracts/src
python -m ruff format --check src tests scripts eval_harness_bridge/src mango-integration-contracts/src
python -m mypy --strict src tests scripts eval_harness_bridge/src mango-integration-contracts/src
python scripts/lint_agent_frontmatter.py
python -m pytest -q
python scripts/check_coverage.py
python -m coverage run --source=eval_harness_bridge/src -m pytest `
    tests/eval_harness_bridge -o addopts="" -q
python -m coverage report --show-missing --fail-under=100
python -m coverage run --source=mango-integration-contracts/src -m pytest `
    tests/mango_contracts -o addopts="" -q
python -m coverage report --show-missing --fail-under=100
```

Hook parity (`ruff` / `mypy` revs are exact-pinned in `pyproject.toml` in
lockstep with `.pre-commit-config.yaml`):

```powershell
make precommit     # pre-commit run --all-files
```
