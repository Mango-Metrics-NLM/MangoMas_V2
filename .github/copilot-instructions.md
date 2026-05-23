# Mango-Mas V2 — Copilot Workspace Instructions

## Project Identity
Mango-Mas V2 is a **local-first, modular agent platform** (FastAPI + LM Studio, Python 3.11+).
Architecture is protocol-based with a composition root; all adapters satisfy `@runtime_checkable Protocol` types.

---

## Language & Tooling

- **Python 3.11+** — `from __future__ import annotations` in every `.py` file
- **Pydantic v2** — use `model_validate_json`, `model_dump_json`, `model_json_schema()`; never `.dict()` or `.parse_obj()`
- **FastAPI 0.115+** — lifespan context manager; dependency injection via `Annotated[]`
- **ruff** strict: `select = ["E","F","I","B","UP","SIM","PL","RUF","S","SLF","ARG"]`, `ignore = ["PLR0913"]`
- **mypy strict=true** — no `type: ignore` without a justification comment
- **pytest-asyncio `asyncio_mode="auto"`** — `async def` tests, no `@pytest.mark.asyncio` decorator

---

## Coding Standards

### Always
- `from __future__ import annotations` at the top of every file
- Cross-layer imports go inside `if TYPE_CHECKING:` blocks
- New adapters must satisfy the relevant `Protocol` in `adapters/*/base.py`
- New agents must satisfy `Agent` in `core/agent.py` and be registered in `composition.py`
- All tunables in `Settings` (`config.py`) — no hard-coded URLs, model names, or limits
- `asyncio.to_thread` for any synchronous I/O inside async functions
- OpenTelemetry spans via `get_tracer(__name__).start_as_current_span("...")`

### Never
- Import concrete adapter implementations outside `composition.py`
- Add `@pytest.mark.asyncio` to async test functions
- Use `unittest.mock.patch` on internal protocols — use `Fake*` classes from `tests/fakes.py`
- Hard-code magic numbers or strings in tests — use `tests/constants.py`
- Use `.dict()`, `.parse_obj()`, or other Pydantic v1 APIs

---

## Testing Rules

- Coverage gate: **85 % minimum** (enforced by `pytest --cov`)
- Every new module gets a `tests/test_<module>.py`
- Use `FakeLLM`, `FakeRepository`, `FakeTool`, `FakeMemoryRepository` from `tests/fakes.py`
- Constants from `tests/constants.py`; update file when adding new domain constants
- Property-based / fuzz tests use `hypothesis`
- Integration tests in `tests/integration/` gated by `RUN_INTEGRATION=1`

---

## Error Handling

- Raise typed subclasses of `MangomasError` (never bare `Exception`)
- HTTP status mapping lives exclusively in `api/app.py::_ERROR_STATUS`
- New error types require: class in `errors.py` + entry in `_ERROR_STATUS` + test in `test_errors.py`

---

## File & Folder Conventions

```
src/mangomas/core/       # Stable public contracts — backward-compat required
src/mangomas/agents/     # Agent implementations
src/mangomas/adapters/   # Infrastructure adapters (LLM, storage)
src/mangomas/api/        # HTTP surface
src/mangomas/cli/        # CLI surface
src/mangomas/composition.py  # Single wiring point
tests/                   # Mirrors src/ structure; fakes.py + constants.py are shared
```

---

## Claude Code Agents & Skills

- Parent agents live at `.github/agents/<parent>.agent.md` (architect, backend,
  test-engineer, api-dev).
- Sub-agents are declared via the optional `sub_agents:` frontmatter key on a
  parent and live at `.github/agents/<parent>/<name>.agent.md`. Parents without
  the key remain valid.
- Skills live at `.github/skills/<name>/SKILL.md`. See `mango-testing` for the
  canonical layout; others cover adapter / agent-add / error / observability /
  config / topology / release workflows.
- Use the most specific sub-agent when working in its domain; defer to the
  parent for cross-cutting reviews.

## PR Workflow

- Open PRs as draft and fill the `.github/PULL_REQUEST_TEMPLATE.md` sections
  (Summary, Changes, Test plan, ADR, CHANGELOG, Sub-agent reviews).
- Architectural changes (new boundaries, provider swaps, composition-root edits,
  breaking contracts) require an ADR copied from `docs/adr/_template.md`.
- The `pr-watcher` sub-agent (under architect) drives the documented
  `subscribe_pr_activity` flow for follow-up events.

---

## Commit & PR Standards

- Conventional commits: `feat:`, `fix:`, `test:`, `refactor:`, `chore:`
- Every feature PR includes: implementation + tests + CHANGELOG entry
- Breaking changes documented in CHANGELOG under `### Breaking Changes`
