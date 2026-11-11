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
| Config JSON | `make validate-config` | `.mcp.json` / `.claude/settings.json` / `.claude/settings.local.json.example` |
| Unit suite + global floor | `make test` | `pyproject.toml` addopts mirror the global floor |
| Per-package floors | `make coverage` | **`scripts/check_coverage.py` — the authoritative gate** |
| Scripts floor | `make scripts-coverage` | Makefile `SCRIPTS_FLOOR` |
| Bridge floor (100%) | `make bridge-coverage` | `eval_harness_bridge` is gated separately |
| Contracts floor (100%) | `make contracts-coverage` | `mango-integration-contracts` is gated separately |
| Mypy (strict) | `make typecheck` | 0 errors required |
| Ruff lint + format | `make lint` / `make format-check` | Clean required |
| Import-linter | `make lint-imports` | `core` ↛ outer layers; `workflow`/`eval`/`rag`/`cognitive` independence |
| Frontmatter lint | `make frontmatter` | `scripts/lint_agent_frontmatter.py` |
| Protected paths | `make protected-paths` | `BREAKING-CHANGE` trailer on core contracts |
| Everything above | `make gate` | Mirrors `.github/workflows/ci.yml` |

`pyproject.toml`'s `--cov-fail-under` **mirrors** the global floor for local
runs; `scripts/check_coverage.py` is the single source of truth, because it is
the only place that also enforces the per-package floors.

### Running the baseline

```powershell
# From the repo root with the venv activated
make gate          # validate-config lint format-check typecheck lint-imports
                   # frontmatter protected-paths test coverage
                   # bridge-coverage contracts-coverage scripts-coverage
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
| Cognitive producer | `tests/cognitive/` | Flag-off identity, JSONL/HTTP/composite sinks, INV-16 PDP refuse-don't-strip (byte-identical projections), role map, retrieve-only, GenAI span exporter, planner step cap, camelCase authority keys |
| Gated-suite parity | `tests/deploy/test_gated_suite_homes.py` | Every `RUN_*` suite has an executing home or a recorded infeasibility reason — and never both |
| Hardware contract | `tests/tooling/test_e2e_hardware_contract.py` | Lints tiers 2/3 for elapsed-time assertions, numeric timeouts, device literals, embedding equality |
| Origin-delta AQA (2026-10-29) | `tests/regression/test_aqa_20261029_defects.py` | spec-0039 D-1..D-6: live step budget ≥ adapter budget, `ToolAgent` without a registry, `test_auth` header typing, nested-body / malformed-SSE typing, CLI settings errors → exit 2; register in `docs/testing/rca-20261029.md` |
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
| `utils` | 100% |
| `headers` | 100% |
| `entry_points` | 100% |
| `composition` | 100% |
| `cli` | 100% |
| `config` | 100% |
| `telemetry` | 100% |
| `metrics` | 100% |
| `eval` | 100% |
| `workflow` | 100% |
| `harness` | 100% |
| `agents` | 98% |
| `api` | 98% |
| `rag` | 98% |
| `cognitive` | 98% |
| `adapters` | 98% (embeddings/vector adapters covered via injected fakes; the optional-SDK import paths carry a reasoned `# pragma: no cover - requires <extra>`) |
| **Global** | **98%** |

Floors are set at the measured value (branch coverage, 2026-11-05): 100% where
the package measures 100%, 98% where a stray partial branch is plausible. A
`# pragma: no cover` outside a `TYPE_CHECKING` guard must state its reason on
the same line (`tests/test_check_coverage.py`); the eight "defensive" pragmas
that used to exist were all reachable and now have tests.

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

## LM Studio E2E (14 suites)

Gated on `RUN_LMSTUDIO=1`. Last full run 2026-11-01 (spec-0041): 25/25
passed with `nvidia/nemotron-3-nano-omni` (base), `liquid/lfm2-24b-a2b`
(override/review) and `text-embedding-nomic-embed-text-v1.5`.

| Scenario | Test file |
|---|---|
| Ping (smoke) | `tests/lmstudio/test_smoke.py` |
| Chat invoke | `tests/lmstudio/test_chat_invoke.py` |
| Chat stream SSE | `tests/lmstudio/test_chat_stream.py` |
| Stream fallback | `tests/lmstudio/test_stream_fallback.py` |
| Streamed turn persists | `tests/lmstudio/test_stream_persists.py` |
| Summarize invoke | `tests/lmstudio/test_summarize_invoke.py` |
| Unknown model 502 | `tests/lmstudio/test_unknown_model.py` |
| Per-step timeout | `tests/lmstudio/test_step_timeout.py` |
| Embeddings batch | `tests/lmstudio/test_embeddings.py` |
| Per-agent `MODEL_OVERRIDE` (spec-0028) | `tests/lmstudio/test_model_override.py` |
| Pipeline acceptance loop (spec-0027) | `tests/lmstudio/test_pipeline_acceptance.py` |
| Plan → execute → review graph | `tests/lmstudio/test_plan_execute_review.py` |
| Workflow run over HTTP | `tests/lmstudio/test_workflow_run.py` |
| Named LLM profiles + routing oracle (spec-0040/0041) | `tests/lmstudio/test_llm_profiles.py` |

```powershell
$env:RUN_LMSTUDIO = '1'
$env:LMSTUDIO_MODEL = 'nvidia/nemotron-3-nano-omni'          # your loaded chat model
$env:LMSTUDIO_OVERRIDE_MODEL = 'liquid/lfm2-24b-a2b'         # second model; unset skips those scenarios
$env:LMSTUDIO_EMBEDDING_MODEL = 'text-embedding-nomic-embed-text-v1.5'  # unset fails with HTTP 400
python -m pytest tests/lmstudio --no-cov -q
```

The Studio consumer contract runs against the same server with
`make studio-probe`. Live mode reports the four model-naming checks as
`SKIP` (26 passed, 4 skipped); `make studio-probe-mock` asserts all 30.

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

### CUDA placement gate (`make gpu`, spec-0039)

Ranking parity cannot tell a CUDA run from a silent CPU fallback: both rank
identically. `tests/rag/test_device_resolution_gpu.py` asserts the device the
model actually loaded on: auto-detect lands on `cuda`, and every visible index
loads there with CPU ranking parity. With `RUN_GPU=1` and no visible device it
**fails**; it never skips. Keep it off CPU-only runners.

```powershell
$env:RUN_GPU = '1'; $env:RUN_EMBEDDINGS_LOCAL = '1'; $env:RUN_RAG = '1'
python -m pytest -m gpu tests/rag -q   # make gpu
```

Install the CUDA torch build **after** the extras, then confirm with
`python -c "import torch; print(torch.cuda.is_available())"`. Installing
`.[dev,...]` over `torch==2.11.0+cu128` silently replaces it with PyPI's
CPU-only wheel on Windows, because the cu128 build caps `setuptools<82` and
`[dev]` floors `setuptools>=83`. The cu130 build (`--index-url
https://download.pytorch.org/whl/cu130`) has no such cap and resolves cleanly.

### Guard falsifiability (`make guard-probe`, spec-0033)

`scripts/guard_probe.py` reads `scripts/guard_probe_manifest.json`, applies
each mutation, requires the named guard to exit non-zero, restores the
original bytes, and requires the guard to exit zero. It refuses a dirty tree
unless `--allow-dirty` is passed. It is not a dependency of `make gate`.

The manifest holds three families. `MUT-AQA-*` covers the spec-0039 defects.
`MUT-0040-M01`..`M14` replay the fourteen provider-profile mutations
recorded on PR #104 (spec-0040). Those include the precedence swap, both
allowlist validators, the stream pre-check, mixin order, and stamp-before-persist.
`MUT-STUDIO-01`/`02` drop the request from `ChatAgent`'s invoke and stream
`resolve_llm` calls. Their gate is the Studio consumer probe's mock run
(spec-0041), whose stub reply names the serving model. Live LM Studio never
does, so the stream-path regression is invisible to `make lmstudio`.
`MUT-STUDIO-03` turns the live routing `SKIP` back into a `PASS`, and its gate
must go red. A check that cannot fail must not count as passed.

Before each gate the probe evicts the target's `.pyc`, and the restore puts back
the original mtime as well as the bytes. A same-size mutation restored within
one second is otherwise served from stale bytecode
(`test_drop_bytecode_evicts_a_cache_compiled_from_the_defect`). Each gate selects
a single test, so a mutation that broke collection shows up as an error rather
than a pass. A guard is identified by the mutated file plus the test its gate
selects, so the same proof cannot be listed twice under different ids.

```powershell
python scripts/guard_probe.py --allow-dirty   # this checkout is dirty
make guard-probe                               # clean tree
```

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

**Hooks ≠ gate.** Pre-commit is ruff, mypy (`src/` only), frontmatter,
`validate-config`, and `lint-imports`. The Stop hook is
`make typecheck format-check` plus `pytest --no-cov`. Neither includes
coverage floors or `protected-paths`. `make gate` remains the pre-PR bar.
A `pre-commit run --all-files` **CI job** is still deferred.

