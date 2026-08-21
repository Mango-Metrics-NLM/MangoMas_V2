"""Contract tests binding `.env.example` and CLAUDE.md's config table to `Settings`.

Both files are hand-maintained catalogues of `MANGOMAS_*` names, and nothing
checked either against the real settings tree before this. The drift that
followed was recorded in NEXT_STEPS.md and shipped for months: `.env.example`
documented `MANGOMAS_LLM__PROJECT` (the field is `project_id`) and
`MANGOMAS_LLM__MAX_OUTPUT_TOKENS` (no such field). Neither raised — pydantic's
nested sub-models inherit `extra="ignore"`, so an unknown nested name is
silently dropped rather than rejected.

That is why these tests assert **name-resolves-to-a-declared-field** rather
than "constructing Settings raises": the failure mode being guarded is a name
that quietly does nothing, not one that explodes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from mangomas.config import AgentSettings, Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"

_PREFIX = "MANGOMAS_"
_NESTED_DELIMITER = "__"
# `Settings.agents` is `dict[str, AgentSettings]`, so the middle segment of
# MANGOMAS_AGENTS__<NAME>__<FIELD> is a free-form agent slug, not a field. A
# plain `model_fields` walk cannot resolve it. Exempting everything under
# AGENTS__ would also exempt the per-agent fields themselves, so the dict is
# special-cased to validate the trailing field name against AgentSettings.
_AGENTS_FIELD = "AGENTS"

# Placeholder names used in prose as "add a new tunable" templates rather than
# as real settings. Kept explicit so a genuine typo cannot hide behind them.
_DOC_PLACEHOLDERS = frozenset(
    {
        "MANGOMAS_LLM__RETRY_COUNT",
        "MANGOMAS_FEATURE__ENABLED",
        "MANGOMAS_FEATURE__MODE",
        "MANGOMAS_FOO__BAR",
    }
)
# A real Claude Code settings.json env var, not a pydantic Settings field.
_NON_SETTINGS_ENV = frozenset({"MANGOMAS_DISABLE_RTK_HOOK"})

_ENV_NAME_RE = re.compile(r"\bMANGOMAS_[A-Z0-9_]+\b")
# Prose writes the per-agent slug as a placeholder: MANGOMAS_AGENTS__<NAME>__X.
# Substituting a sentinel keeps the name whole, so the trailing field is still
# validated instead of the match stopping dead at the `<`.
_PLACEHOLDER_RE = re.compile(r"<[A-Za-z_]+>")
_PLACEHOLDER_SLUG = "SLUG"


def _declared_names() -> set[str]:
    """Return every `MANGOMAS_*` name the settings tree actually declares.

    Per-agent names are returned in their `MANGOMAS_AGENTS__<FIELD>` shape with
    the slug elided; `_normalize` collapses a real name to match.
    """
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for sub in annotation.model_fields:
                names.add(f"{_PREFIX}{name.upper()}{_NESTED_DELIMITER}{sub.upper()}")
        else:
            names.add(f"{_PREFIX}{name.upper()}")
    for sub in AgentSettings.model_fields:
        names.add(f"{_PREFIX}{_AGENTS_FIELD}{_NESTED_DELIMITER}{sub.upper()}")
    return names


def _normalize(name: str) -> str:
    """Collapse `MANGOMAS_AGENTS__<SLUG>__<FIELD>` to `MANGOMAS_AGENTS__<FIELD>`."""
    parts = name.split(_NESTED_DELIMITER)
    if len(parts) == 3 and parts[0] == f"{_PREFIX}{_AGENTS_FIELD}":
        # `MANGOMAS_AGENTS__<NAME>__*` collapses to the bare group prefix, which
        # `_is_group_prefix` then validates; a concrete field keeps its name.
        return f"{parts[0]}{_NESTED_DELIMITER}{parts[2]}"
    return name


def _names_in(path: Path) -> set[str]:
    text = _PLACEHOLDER_RE.sub(_PLACEHOLDER_SLUG, path.read_text(encoding="utf-8"))
    found = set(_ENV_NAME_RE.findall(text))
    return found - _DOC_PLACEHOLDERS - _NON_SETTINGS_ENV


def _is_group_prefix(name: str) -> bool:
    """True for a bare `MANGOMAS_<GROUP>__` written in prose to name a group.

    Both files legitimately do this (e.g. "configured by HarnessSettings (env
    prefix `MANGOMAS_HARNESS__`)"). It is checked against the real group list
    below rather than skipped, so a misspelled group prefix still fails.
    """
    return name.endswith(_NESTED_DELIMITER)


def _settings_groups() -> set[str]:
    groups = {
        f"{_PREFIX}{name.upper()}{_NESTED_DELIMITER}"
        for name, field in Settings.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    }
    # `agents` is a dict, not a nested model, so the comprehension above misses
    # it — but `MANGOMAS_AGENTS__` is a real prefix operators use.
    groups.add(f"{_PREFIX}{_AGENTS_FIELD}{_NESTED_DELIMITER}")
    return groups


def _unresolved(path: Path) -> list[str]:
    declared = _declared_names()
    groups = _settings_groups()
    unresolved: list[str] = []
    for name in _names_in(path):
        normalized = _normalize(name)
        if _is_group_prefix(normalized):
            if normalized not in groups:
                unresolved.append(name)
        elif normalized not in declared:
            unresolved.append(name)
    return sorted(unresolved)


# ── Self-guards ───────────────────────────────────────────────────────────────
# Every assertion below is over a parsed set. A parser that silently yields
# nothing would make all of them vacuously true — the exact failure mode
# tests/tooling/test_corpus_contract.py documents, where a typo'd relpath
# "used to yield a green *skip*".


def test_env_example_exists_and_parses() -> None:
    assert _ENV_EXAMPLE.is_file()
    assert _names_in(_ENV_EXAMPLE), "parsed zero MANGOMAS_* names from .env.example"


def test_claude_md_config_table_parses() -> None:
    assert _CLAUDE_MD.is_file()
    assert _names_in(_CLAUDE_MD), "parsed zero MANGOMAS_* names from CLAUDE.md"


def test_settings_tree_is_non_empty() -> None:
    assert _declared_names()


# ── The contract ──────────────────────────────────────────────────────────────


def test_env_example_names_all_resolve() -> None:
    """Every documented name maps to a declared field.

    A name that does not is silently ignored at runtime, so an operator who
    sets it gets no error and no effect.
    """
    assert _unresolved(_ENV_EXAMPLE) == []


def test_claude_md_names_all_resolve() -> None:
    """CLAUDE.md auto-loads into every session, so a wrong name there is read
    before any work starts."""
    assert _unresolved(_CLAUDE_MD) == []


@pytest.mark.parametrize(
    "group",
    sorted(
        name.upper()
        for name, field in Settings.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    ),
)
def test_env_example_documents_every_settings_group(group: str) -> None:
    """A whole group missing from `.env.example` is the drift that hid RAG,
    workflow, tenancy, auth, CORS and backpressure from operators entirely."""
    prefix = f"{_PREFIX}{group}{_NESTED_DELIMITER}"
    assert any(n.startswith(prefix) for n in _names_in(_ENV_EXAMPLE)), (
        f"no {prefix}* variable documented in .env.example"
    )


def test_env_example_does_not_reintroduce_the_known_drift() -> None:
    """Named regression guard for the two defects NEXT_STEPS.md recorded.

    They are already covered by `test_env_example_names_all_resolve`; naming
    them means a reintroduction reports *what* came back, not just that
    something did.
    """
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "MANGOMAS_LLM__PROJECT=" not in text, "MANGOMAS_LLM__PROJECT is MANGOMAS_LLM__PROJECT_ID"
    assert "MANGOMAS_LLM__MAX_OUTPUT_TOKENS" not in text, "no such field on LLMSettings"


def test_env_file_is_not_read_during_these_assertions() -> None:
    """`Settings.model_config` sets `env_file=".env"`, so a contributor's local
    `.env` would otherwise leak into the tree these tests validate against.

    The suite reads `model_fields` (a class attribute) rather than constructing
    `Settings`, so no env file is consulted at all. This pins that: constructing
    with an explicit `_env_file=None` yields the same field set.
    """
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert set(type(settings).model_fields) == set(Settings.model_fields)
