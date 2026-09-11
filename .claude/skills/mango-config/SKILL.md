---
name: mango-config
description: >
  Working with Mango-Mas V2 settings and secrets. Use when: adding a new
  Settings group, adding a new tunable to an existing group, wiring up a
  new env var, swapping the secrets provider, or diagnosing why an env
  value isn't taking effect. Covers the MANGOMAS_ env prefix, nested
  delimiter convention, DEFAULT_* module constants, and the
  SecretsProvider seam.
argument-hint: "Describe the new setting (e.g. 'add a retry-count to LLMSettings') or paste the env var that isn't being read"
---

# Mango-Mas Config Skill

## When to Use

- Add a new tunable to `LLMSettings`, `DBSettings`, `MemorySettings`, `LogSettings`, or `LoopSettings`
- Introduce a brand new settings group (e.g. `HarnessSettings`, `SignalSettings`)
- Add a new `SecretsProvider` implementation (env, GCP, Vault)
- Diagnose why `MANGOMAS_FOO__BAR=baz` isn't taking effect
- Update `.env.example` to document a new variable

---

## Quick Commands

```powershell
# Verify settings parse with env overrides
python -m pytest tests/test_config.py -v

# Print effective settings (useful when env vars seem ignored)
python -c "from mangomas.config import get_settings; import json; print(get_settings().model_dump_json(indent=2))"

# Run with overrides
$env:MANGOMAS_LLM__BASE_URL='http://localhost:1235/v1' ; python -m mangomas.cli.main chat "hello"
```

```bash
python -m pytest tests/test_config.py -v
python -c "from mangomas.config import get_settings; print(get_settings().model_dump_json(indent=2))"
MANGOMAS_LLM__BASE_URL=http://localhost:1235/v1 python -m mangomas.cli.main chat "hello"
```

---

## Config Rules

| Rule | Detail |
|------|--------|
| Env prefix | All env vars use `MANGOMAS_` prefix. |
| Nested delimiter | Use `__` between group and field: `MANGOMAS_LLM__BASE_URL`, `MANGOMAS_HARNESS__ENABLED`, `MANGOMAS_SIGNAL__ENABLED`. |
| `DEFAULT_*` module constants | Every default value is a module-level `DEFAULT_*` constant in the group module that owns it (`config/llm.py`, `config/rag.py`, ...) — the single source of truth. |
| Constants re-export, not restate | `tests.constants` re-exports config-mirroring defaults from `mangomas.config` (`from mangomas.config import DEFAULT_X as DEFAULT_X`), so a config change can never silently desync the tests. Genuinely test-scoped values (mock URLs, env-var names, fixtures) stay as literals in the domain modules. |
| BaseModel sub-groups | Each settings group is a `BaseModel` (not `BaseSettings`) attached to root `Settings` via `Field(default_factory=...)`. |
| Backwards-compatible | New fields must have defaults. Renames go through deprecation alias. |
| Secrets seam | Secret values resolve through `_resolve_llm_secrets()` in `composition/secrets.py`, never read directly from env in adapters. |
| `get_settings()` is cached | Memoised via `lru_cache`; tests use `monkeypatch.setenv` then `get_settings.cache_clear()`. |

---

## Reference

| File | Role |
|------|------|
| `src/mangomas/config/` | One module per settings domain; each holds its `*Settings` class and the `DEFAULT_*` constants it reads |
| `src/mangomas/config/__init__.py` | Permanent re-export facade — `from mangomas.config import X` keeps working for every name (ADR-0019 / spec-0015) |
| `src/mangomas/config/_root.py` | The top-level `Settings` aggregate and `get_settings` |
| `.env.example` | Documented env vars with defaults |
| `src/mangomas/secrets/provider.py` | `SecretsProvider` Protocol |
| `src/mangomas/secrets/env.py` | `EnvSecretsProvider` (default) |
| `src/mangomas/secrets/registry.py` | `secrets_registry` registration point |
| `src/mangomas/composition/secrets.py` | `_resolve_llm_secrets()` — the only place secret refs are resolved |
| `tests/test_config.py` | Reference test for defaults + env overrides + nested groups |
| `tests/constants/` | Re-exports config-mirroring `DEFAULT_*` from `mangomas.config` via `X as X` on the package facade (`tests.constants`); only test-scoped values (mock URLs, env-var names, fixtures) are literals in the domain modules |

---

## Template — Add a New Tunable

```python
# src/mangomas/config/<group>.py — with the class that reads it
DEFAULT_LLM_RETRY_COUNT: int = 3

# Inside the LLMSettings class
class LLMSettings(BaseModel):
    # ...existing fields...
    retry_count: int = DEFAULT_LLM_RETRY_COUNT
```

Document it in `.env.example`:

```
# Number of retries for transient LLM failures (default: 3)
MANGOMAS_LLM__RETRY_COUNT=3
```

Add a test in `tests/test_config.py`:

```python
def test_llm_retry_count_defaults_to_constant() -> None:
    s = LLMSettings()
    assert s.retry_count == DEFAULT_LLM_RETRY_COUNT

def test_llm_retry_count_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_LLM__RETRY_COUNT", "7")
    get_settings.cache_clear()
    assert get_settings().llm.retry_count == 7
```

---

## Template — Add a New Settings Group

```python
# src/mangomas/config/<group>.py
DEFAULT_FEATURE_ENABLED: bool = False
DEFAULT_FEATURE_MODE: Literal["a", "b"] = "a"


class FeatureSettings(BaseModel):
    enabled: bool = DEFAULT_FEATURE_ENABLED
    mode: Literal["a", "b"] = DEFAULT_FEATURE_MODE


# In Settings(BaseSettings):
class Settings(BaseSettings):
    # ...existing fields...
    feature: FeatureSettings = Field(default_factory=FeatureSettings)
```

Env vars: `MANGOMAS_FEATURE__ENABLED=true`, `MANGOMAS_FEATURE__MODE=b`.

---

## Removing an Unused `DEFAULT_*`

A `DEFAULT_*` whose field was dropped is dead weight, but it may still be
referenced from outside the `config/` package. Before deleting:

1. Search every consumer, not just `src/`:
   `grep -rn 'DEFAULT_<NAME>' src tests scripts docs .env.example` — a live hit
   in `.env.example` or `docs/` means the tunable is still documented as public.
2. If it was re-exported in `tests.constants`, drop the `X as X` line there in
   the same commit — a re-export of a deleted name is an `ImportError` at
   collection time, which fails the whole suite rather than one test.
3. Remove the field, the constant, and the `.env.example` line together, then run
   `python -m pytest -q` and `mypy`.

---

## Workflow

1. Decide whether the tunable belongs in an existing group or warrants a new group.
2. Add the `DEFAULT_*` module-level constant first (single source of truth).
3. Add the field on the BaseModel, defaulting to the constant.
4. Update `.env.example` with a one-line comment + default.
5. Add a row to CLAUDE.md's `## Configuration` table (variable, default,
   purpose). Both directions are CI-enforced by
   `tests/deploy/test_env_example_contract.py` (spec-0022 R12): a documented
   name that resolves to nothing fails, and so does a declared field that
   CLAUDE.md omits. Skipping this step turns the suite red.
6. Add two tests: default value + env override.
7. If new group: add a single integration test in `tests/composition/` confirming wiring doesn't break.
8. Run `ruff check --fix`, `mypy --strict`, `pytest`.

---

## Constraints

- DO NOT hardcode the default value inside the model — use `DEFAULT_*` constants.
- DO NOT read `os.environ` directly in any module other than the `config/` package and `secrets/env.py`.
- DO NOT break backwards compatibility — new fields require defaults; renames require alias.
- DO NOT forget `get_settings.cache_clear()` in tests that set env vars.
- DO NOT add a `Settings` field without its CLAUDE.md row — the reverse
  drift test (`test_claude_md_documents_every_settings_field`) fails on it.

---

## Diagnosing Failures

1. Env override "doesn't work" → `get_settings()` is cached; call `get_settings.cache_clear()` or `monkeypatch.setenv` before the first `get_settings()` call.
2. mypy error `Argument has incompatible type` after adding a field → check the field has a default and is annotated.
3. `ValidationError` at startup → env value couldn't coerce; print `get_settings()` to see which field rejected.
4. Secret value is empty in the adapter → confirm the secret ref is resolved in `_resolve_llm_secrets()` and the `SecretsProvider` is registered.
