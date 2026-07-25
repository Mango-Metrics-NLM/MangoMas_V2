# Tests — `tests/`

Unit, property-based, and integration tests for Mango-Mas V2.

## Structure

Root-level `test_<module>.py` files mirror the module under test. Directories
group a subsystem or an env-gated suite. Run `ls tests/` for the full list —
the shape, not an exhaustive inventory, is below.

```
tests/
├── fakes.py               # Shared fake adapters (FakeLLM, FakeRepository, FakeTool, ...)
├── constants.py           # Shared constants — see "Constants contract" below
├── conftest.py            # pytest fixtures (fake_llm, fake_repo, fake_memory, fake_tool)
├── _script_loader.py      # Shared helper for importing scripts/*.py in tests
├── test_<module>.py       # One per src module: agent, api, cli, composition, config,
│                          #   control_loop, correlation, errors, lmstudio, memory,
│                          #   orchestrator, planner, postgres, registry, reviewer,
│                          #   secrets, sqlite, telemetry, tools, topologies, ...
├── test_workflow_*.py     # Workflow graph: model, predicate, loader, registry,
│                          #   executor, branch, composite fan_out, API, CLI, settings
├── adapters/              # Adapter units + shared helpers
│   ├── test_shared_errors.py     # _http_errors / _vertex_errors translation
│   ├── test_openai_client.py     # OpenAICompatHTTPClient lifecycle contract
│   ├── embeddings/               # lmstudio (respx), sentence_transformers, vertex
│   └── vector/                   # ChromaVectorStore via injected fake collection
├── agents/                # Agent units
├── eval/                  # Evaluation harness (incl. test_serialize.py)
├── rag/                   # RAG domain (chunker + Hypothesis fuzz, pipeline, retrieval)
├── deploy/                # Deploy-manifest + Docker build-context contracts
├── eval_harness_bridge/   # Bridge black-box tests (own 100% floor, own constants.py)
├── integration/           # Requires RUN_INTEGRATION=1; ASGI transport
├── lmstudio/              # Requires RUN_LMSTUDIO=1; real LM Studio
├── vertex/                # Requires RUN_VERTEX=1; real Vertex project
└── postgres/              # Requires RUN_POSTGRES=1; testcontainers
```

## Current baseline

Test counts and coverage percentages are **not** recorded here — they went
stale every release. `scripts/check_coverage.py` is the authoritative gate
(global 95% plus per-package floors); run `make gate` for the live numbers.

## Constants contract

`tests/constants.py` has two halves:

- **Config-mirroring defaults are re-exported** from `mangomas.config` using
  the explicit `X as X` idiom (e.g. `DEFAULT_LLM_BASE_URL`,
  `DEFAULT_VECTOR_TOP_K`). Never restate a config default as a literal — a
  re-export cannot desync.
- **Test-scoped values are defined locally** (mock URLs, env-var names,
  stubs, fixtures, `TEST_VERTEX_PROJECT`). These have no config counterpart.

`tests/eval_harness_bridge/constants.py` is deliberately separate: the bridge
is tested as a decoupled black-box client and must not import `mangomas`.

## Configuration

```ini
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"   ← async tests work without @pytest.mark.asyncio
```

## Run Commands

```powershell
# All unit tests + coverage gate (95 % minimum)
python -m pytest --tb=short -q

# Single test file
python -m pytest tests/test_tools.py -v

# Coverage with missing-line report
python -m pytest --cov=mangomas --cov-report=term-missing

# Integration tests
$env:RUN_INTEGRATION='1' ; python -m pytest tests/integration --no-cov -q

# LM Studio E2E (7 scenarios)
$env:RUN_LMSTUDIO='1' ; $env:LMSTUDIO_MODEL='google/gemma-4-e4b'
python -m pytest tests/lmstudio --no-cov -q

# Vertex AI E2E (requires model access)
$env:RUN_VERTEX='1' ; $env:VERTEX_PROJECT_ID='your-project'
python -m pytest tests/vertex --no-cov -q

# Postgres (testcontainers, requires Docker)
$env:RUN_POSTGRES='1' ; python -m pytest tests/postgres --no-cov -q

# Per-package coverage floors
python scripts/check_coverage.py
```

## Fake Adapters Quick Reference

```python
from tests.fakes import FakeLLM, FakeRepository, FakeTool, FakeMemoryRepository

# FakeLLM
llm = FakeLLM(reply="default")               # same reply every call
llm = FakeLLM(replies=["first", "second"])   # sequential replies

# FakeRepository — satisfies TurnRepository
repo = FakeRepository()   # repo._turns: list[Turn]

# FakeTool — satisfies Tool protocol
tool = FakeTool(name="echo", result="echo-result")

# FakeMemoryRepository — satisfies MemoryRepository
mem = FakeMemoryRepository()
```

## Writing a New Test File

1. Mirror the source file: `src/mangomas/foo.py` → `tests/test_foo.py`.
2. Import only public surfaces (not private helpers).
3. Use constants from `constants.py` for all string/number literals — see the
   Constants contract above (config defaults are re-exported from
   `mangomas.config`; only test-scoped values are defined locally).
4. Use `async def` for async tests — no `@pytest.mark.asyncio`.
5. One assertion focus per test; name it `test_<scenario>_<expected>`.
6. Run `python -m pytest tests/test_foo.py -v` to verify all pass.
7. Run the full suite to confirm 95 % gate is maintained.

## Adding a Fake Adapter

1. Implement as a `@dataclass` satisfying the target Protocol.
2. Verify with `assert isinstance(FakeXxx(), XxxProtocol)`.
3. Export from `fakes.py` (no `__all__` needed).
4. Add a fixture in `conftest.py` if broadly shared.
