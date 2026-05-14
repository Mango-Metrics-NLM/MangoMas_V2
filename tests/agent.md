# Tests — `tests/`

Unit, property-based, and integration tests for Mango-Mas V2.

## Structure

```
tests/
├── fakes.py           # Shared fake adapters (FakeLLM, FakeRepository, FakeTool, FakeMemoryRepository)
├── constants.py       # Domain constants — use instead of magic strings/numbers
├── conftest.py        # pytest fixtures (fake_llm, fake_repo, fake_memory, fake_tool)
├── test_agent.py      # core/agent.py
├── test_api.py        # api/app.py (httpx.AsyncClient)
├── test_cli.py        # cli/main.py
├── test_composition.py
├── test_config.py
├── test_control_loop.py
├── test_errors.py
├── test_lmstudio.py
├── test_memory.py
├── test_orchestrator.py
├── test_planner.py
├── test_registry.py
├── test_reviewer.py
├── test_sqlite.py
├── test_telemetry.py
├── test_tool_agent.py
├── test_tools.py
├── test_topologies.py
└── integration/       # Requires RUN_INTEGRATION=1; real LM Studio needed
```

## Configuration

```ini
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"   ← async tests work without @pytest.mark.asyncio
```

## Run Commands

```powershell
# All unit tests + coverage gate (85 % minimum)
python -m pytest --tb=short -q

# Single test file
python -m pytest tests/test_tools.py -v

# Coverage with missing-line report
python -m pytest --cov=mangomas --cov-report=term-missing

# Integration tests
$env:RUN_INTEGRATION='1' ; python -m pytest tests/integration --no-cov -q
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
7. Run the full suite to confirm 85 % gate is maintained.

## Adding a Fake Adapter

1. Implement as a `@dataclass` satisfying the target Protocol.
2. Verify with `assert isinstance(FakeXxx(), XxxProtocol)`.
3. Export from `fakes.py` (no `__all__` needed).
4. Add a fixture in `conftest.py` if broadly shared.
