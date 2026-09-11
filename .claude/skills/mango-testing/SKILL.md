---
name: mango-testing
description: >
  Testing workflow for Mango-Mas V2. Use when: running the test suite,
  diagnosing failing tests, writing new tests for a module, extending fake
  adapters in fakes.py, updating tests.constants, checking coverage, or adding
  integration tests. Covers the pytest-asyncio auto mode, Fake* patterns,
  Hypothesis fuzz testing, the tests.constants config re-export contract, and the
  coverage gate.
argument-hint: "Describe the module to test, paste a failing test, or say 'run all tests'"
---

# Mango-Mas Testing Skill

## When to Use

- Run the full unit test suite and interpret results
- Diagnose a test failure or import error
- Write a `tests/test_<module>.py` for a new or modified module
- Extend `tests/fakes.py` with a new fake adapter
- Add domain constants to `tests.constants` (re-export config defaults on the
  package facade; define test-scoped values in the domain modules)
- Verify the coverage gate
- Add or run integration tests gated by `RUN_INTEGRATION=1`

---

## Quick Commands

```powershell
# Pre-PR bar — the full CI chain, including lint-imports and coverage floors
make gate

# Unit tests (fast, no real I/O) — pyproject addopts already supply --cov
python -m pytest -q

# Isolated scripts coverage (Makefile SCRIPTS_FLOOR)
make scripts-coverage

# Import-linter (core ↛ outer layers; sibling independence)
make lint-imports

# Single module
python -m pytest tests/test_<module>.py -v

# Per-package coverage floors — the authoritative gate
python scripts/check_coverage.py

# Which lines are uncovered (only when chasing a specific gap)
python -m pytest --cov-report=term-missing -q

# Integration tests (requires LM Studio running)
$env:RUN_INTEGRATION='1' ; python -m pytest tests/integration --no-cov -q ; Remove-Item Env:\RUN_INTEGRATION

# Lint + type check
ruff check --fix src tests
mypy
```

---

## Project Testing Rules

| Rule | Detail |
|------|--------|
| `asyncio_mode = "auto"` | All async tests are `async def`. Never add `@pytest.mark.asyncio`. |
| No `mock.patch` on protocols | Use `FakeLLM`, `FakeRepository`, `FakeTool`, `FakeMemoryRepository`, `FakeCognitiveSink` from `fakes.py`. |
| No magic values | Strings/numbers in tests come from `tests.constants`. |
| Constants re-export config | A default that mirrors `mangomas.config` is **re-exported**, not restated: `from mangomas.config import DEFAULT_X` in the domain module, then `from tests.constants.config import DEFAULT_X as DEFAULT_X` on the package facade (ruff auto-exempts `__init__.py` from PLC0414). Only test-scoped values — mock URLs, env-var names, fixture payloads, `TEST_VERTEX_PROJECT` — are literals in the domain modules. |
| Coverage gate | `python scripts/check_coverage.py` is the authoritative per-package gate; the pytest `--cov-fail-under` addopt is a coarse pre-filter. |
| One file per module | `tests/test_<module>.py` mirrors `src/mangomas/<module>.py`. |
| Integration gating | `tests/integration/` tests skip unless `RUN_INTEGRATION=1`. |
| Hypothesis | Fuzz/property tests use `from hypothesis import given, strategies as st`. |

---

## Fake Adapters Reference

```python
# tests/fakes.py
FakeLLM(reply="text", replies=["step1", "step2"])  # .calls: list[list[Message]]
FakeRepository()        # ._turns: list; satisfies TurnRepository
FakeTool(name="echo", result="echo-result")  # satisfies Tool protocol
FakeMemoryRepository()  # .episodic_entries, .index_content, .closed
FakeCognitiveSink()     # .emitted; raise_on_emit= to prove handle contains I/O
```

Fixtures are registered in `tests/conftest.py`:
```python
fake_llm, fake_repo, fake_memory, fake_tool
```

---

## Test File Template

```python
"""Tests for <module>."""
from __future__ import annotations

import pytest
from mangomas.<module> import <Subject>
from mangomas.core.agent import AgentContext, AgentRequest, Message
from tests.fakes import FakeLLM, FakeRepository
from tests import constants


def test_<scenario>_<expected_outcome>() -> None:
    ...


@pytest.mark.asyncio  # NOTE: omit this — asyncio_mode=auto handles it
async def test_<async_scenario>() -> None:
    llm = FakeLLM(reply=constants.STUB_REPLY)
    ctx = AgentContext(llm=llm, repo=None)
    ...
```

---

## Adding a New Fake

1. Open `tests/fakes.py`.
2. Add a `@dataclass` satisfying the target Protocol.
3. Export from the file (no `__all__` needed — all names are importable).
4. Add a fixture in `tests/conftest.py` if it should be broadly shared.

---

## Diagnosing Failures

1. Run with `-v` and `--tb=long` to see full tracebacks.
2. Check for `ImportError` — usually a missing `from __future__ import annotations` or circular import.
3. Check for `ValidationError` — Pydantic v1 API used (`.dict()`, `.parse_obj()`).
4. Check for `RuntimeError: no running event loop` — `@pytest.mark.asyncio` was accidentally added.
5. Check coverage with `--cov-report=term-missing` to identify uncovered lines.
