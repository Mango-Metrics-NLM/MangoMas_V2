# Tests — `tests/`

Unit, property-based, and integration tests for Mango-Mas V2.

## Structure

```
tests/
├── fakes.py               # Shared fake adapters (FakeLLM, FakeRepository, FakeTool, FakeMemoryRepository)
├── constants.py           # Domain constants — use instead of magic strings/numbers
├── conftest.py            # pytest fixtures (fake_llm, fake_repo, fake_memory, fake_tool)
├── _script_loader.py      # Shared helper for importing scripts/*.py in tests
├── test_agent.py          # core/agent.py
├── test_api.py            # api/app.py (httpx.AsyncClient)
├── test_cli.py            # cli/main.py
├── test_composition.py    # composition.py (wiring, harness, memory)
├── test_config.py         # config.py (settings parsing, env overrides)
├── test_control_loop.py   # core/loop.py
├── test_correlation.py    # correlation.py (ContextVar, sanitisation)
├── test_errors.py         # errors.py (hierarchy, error_status mapping)
├── test_harness_session_start.py  # scripts/harness_session_start.py
├── test_harness_settings.py       # HarnessSettings defaults + env overrides
├── test_lint_agent_frontmatter.py # scripts/lint_agent_frontmatter.py (15 cases)
├── test_lmstudio.py       # adapters/llm/lmstudio.py (mocked httpx)
├── test_memory.py         # adapters/storage/memory.py
├── test_orchestrator.py   # core/orchestrator.py
├── test_planner.py        # agents/planner.py
├── test_postgres.py       # adapters/storage/postgres.py (22 tests: DSN + mocked asyncpg)
├── test_registry.py       # registry.py
├── test_reviewer.py       # agents/reviewer.py
├── test_secrets.py        # secrets/ (env, GCP provider, registry)
├── test_sqlite.py         # adapters/storage/sqlite.py
├── test_sqlite_concurrency.py  # 50-way async fan-out on SQLite
├── test_telemetry.py      # telemetry.py
├── test_tool_agent.py     # agents/tool_agent.py
├── test_tools.py          # core/tools.py
├── test_topologies.py     # pipeline/fan-out topologies
├── test_vertex_unit.py    # adapters/llm/vertex.py (mocked SDK)
├── eval/                  # Evaluation harness tests (in-process)
├── integration/           # Requires RUN_INTEGRATION=1; ASGI transport
│   ├── test_api_flow.py
│   └── test_gcp_secrets_live.py
├── lmstudio/              # Requires RUN_LMSTUDIO=1; real LM Studio
├── vertex/                # Requires RUN_VERTEX=1; real Vertex project
└── postgres/              # Requires RUN_POSTGRES=1; testcontainers
```

## Current baseline (v0.3.1)

| Metric | Value |
|---|---|
| Tests passed | 515 |
| Tests skipped | 18 (integration/LM Studio/Vertex/Postgres — gated) |
| Global coverage | 98.16% |
| Coverage floor | 95% |

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
3. Use constants from `constants.py` for all string/number literals.
4. Use `async def` for async tests — no `@pytest.mark.asyncio`.
5. One assertion focus per test; name it `test_<scenario>_<expected>`.
6. Run `python -m pytest tests/test_foo.py -v` to verify all pass.
7. Run the full suite to confirm 95 % gate is maintained.

## Adding a Fake Adapter

1. Implement as a `@dataclass` satisfying the target Protocol.
2. Verify with `assert isinstance(FakeXxx(), XxxProtocol)`.
3. Export from `fakes.py` (no `__all__` needed).
4. Add a fixture in `conftest.py` if broadly shared.
