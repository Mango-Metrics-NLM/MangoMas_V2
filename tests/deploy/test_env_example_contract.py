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

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from mangomas.config import AgentSettings, Settings
from tests.constants.corpus import ROOT_INSTRUCTION_RELPATHS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
# The root instruction pair as one document: CLAUDE.md's first line is
# `@AGENTS.md`, so a session is told the union. Reading either half alone
# would let a row move across the split and vanish from this contract while
# both files still look healthy.
_INSTRUCTION_DOCS = tuple(_REPO_ROOT / rel for rel in ROOT_INSTRUCTION_RELPATHS)

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


def _instruction_text() -> str:
    """The root instruction pair, concatenated and placeholder-normalised."""
    return _PLACEHOLDER_RE.sub(
        _PLACEHOLDER_SLUG,
        "\n".join(doc.read_text(encoding="utf-8") for doc in _INSTRUCTION_DOCS),
    )


def _names_in_instructions() -> set[str]:
    found = set(_ENV_NAME_RE.findall(_instruction_text()))
    return found - _DOC_PLACEHOLDERS - _NON_SETTINGS_ENV


def _unresolved_in_instructions() -> list[str]:
    declared = _declared_names()
    groups = _settings_groups()
    unresolved: list[str] = []
    for name in _names_in_instructions():
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


def test_instruction_docs_config_tables_parse() -> None:
    assert all(doc.is_file() for doc in _INSTRUCTION_DOCS)
    assert _names_in_instructions(), "parsed zero MANGOMAS_* names from CLAUDE.md"


def test_settings_tree_is_non_empty() -> None:
    assert _declared_names()


# ── The contract ──────────────────────────────────────────────────────────────


def test_env_example_names_all_resolve() -> None:
    """Every documented name maps to a declared field.

    A name that does not is silently ignored at runtime, so an operator who
    sets it gets no error and no effect.
    """
    assert _unresolved(_ENV_EXAMPLE) == []


def test_instruction_doc_names_all_resolve() -> None:
    """CLAUDE.md auto-loads into every session, so a wrong name there is read
    before any work starts."""
    assert _unresolved_in_instructions() == []


# Settings fields intentionally undocumented in CLAUDE.md's config tables.
# `MANGOMAS_AGENTS` is the dict field behind the documented per-agent
# `MANGOMAS_AGENTS__<NAME>__*` rows — the nested form IS its documentation.
# Kept explicit so any further exemption is a reviewed edit, not a regex hole.
_INSTRUCTIONS_UNDOCUMENTED_OK: frozenset[str] = frozenset({f"{_PREFIX}{_AGENTS_FIELD}"})


def test_instruction_docs_document_every_settings_field() -> None:
    """Reverse direction of the resolve test: every declared field is documented.

    The forward tests catch a documented name that resolves to nothing; until
    spec-0022 R12 nothing caught the opposite drift — a real Settings field
    (29 of them, at the time this landed) invisible to every session that
    auto-loads CLAUDE.md. A field CLAUDE.md omits effectively does not exist
    for agent work.
    """
    documented = {_normalize(name) for name in _names_in_instructions()}
    missing = sorted(_declared_names() - documented - _INSTRUCTIONS_UNDOCUMENTED_OK)
    assert missing == [], f"Settings fields missing from CLAUDE.md's config tables: {missing}"


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


# ── Documented defaults must match the model (spec-0023 R3) ───────────────────
#
# CLAUDE.md's Key Design Rules cite this file as the mechanical enforcement for
# "No hard-coded values". Until now both directions were **name-only**: ~96
# rows carry a `| Default |` column that nothing compared against
# `Settings`. Defaults are precisely what rots — a field's default changes in
# code and the auto-loaded doc keeps asserting the old one to every session.

_ROW_RE = re.compile(r"^\|\s*`(MANGOMAS_[A-Z0-9_]+)`\s*\|\s*(.+?)\s*\|", re.MULTILINE)
# Rows whose Default cell is prose rather than a value.
_NOT_A_VALUE = frozenset({"_(none)_", "—", "-", ""})
# Per-agent rows document the *effective* default an agent falls back to when
# the field is unset (`AgentSettings.max_tool_steps` is None; `ToolAgent`
# supplies DEFAULT_TOOL_MAX_STEPS). Comparing those to the field default would
# compare two different things. Listed explicitly because they were previously
# skipped by accident — `Settings.agents` is a `dict`, not a nested model, so
# the walk below never emitted them and nothing recorded why.
_EFFECTIVE_NOT_FIELD_DEFAULT: frozenset[str] = frozenset(
    {
        f"{_PREFIX}{_AGENTS_FIELD}{_NESTED_DELIMITER}MAX_TOOL_STEPS",
        f"{_PREFIX}{_AGENTS_FIELD}{_NESTED_DELIMITER}HISTORY_LIMIT",
    }
)
# Fields with a real default that legitimately have no table row of their own.
# Explicit, because the reverse-direction check below is only as strong as the
# list of things it agrees to ignore.
_DEFAULT_UNDOCUMENTED_OK: frozenset[str] = frozenset(
    {
        # `Settings.agents` is a dict keyed by agent name, so the documented
        # rows are the per-agent placeholders (`MANGOMAS_AGENTS__<NAME>__*`).
        # A row for the bare container would document nothing a reader can set.
        f"{_PREFIX}{_AGENTS_FIELD}",
    }
)


def _documented_defaults() -> dict[str, str]:
    text = _instruction_text()
    found: dict[str, str] = {}
    for name, raw_cell in _ROW_RE.findall(text):
        cell = raw_cell.strip()
        if cell in _NOT_A_VALUE:
            continue
        # Strip markdown code fencing and any trailing prose after the value.
        match = re.match(r"`([^`]*)`", cell)
        if match is not None:
            found[_normalize(name)] = match.group(1)
    return found


def _field_default(field: object) -> object:
    """Return a field's *declared* default, resolving a default_factory."""
    default = getattr(field, "default", PydanticUndefined)
    if default is not PydanticUndefined:
        return default
    factory = getattr(field, "default_factory", None)
    return factory() if factory is not None else PydanticUndefined


def _model_defaults() -> dict[str, str]:
    """Flatten Settings into ``MANGOMAS_GROUP__FIELD -> rendered default``.

    Reads the **declared** defaults off ``model_fields``, never a constructed
    ``Settings()``. An instance absorbs ``os.environ`` even with
    ``_env_file=None`` — and this repo's own ``.claude/settings.json`` exports
    ``MANGOMAS_LOG__FORMAT=json`` into every Claude Code session, so an
    instance-based reader reported the session's value as "the default": green
    locally, red in CI, and masking a genuinely wrong doc row. A contract test
    whose verdict depends on the ambient environment is not a contract test.
    """
    flat: dict[str, str] = {}
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for sub, sub_field in annotation.model_fields.items():
                value = _field_default(sub_field)
                if value is not PydanticUndefined:
                    key = f"{_PREFIX}{name.upper()}{_NESTED_DELIMITER}{sub.upper()}"
                    flat[key] = _render(value)
        else:
            value = _field_default(field)
            if value is not PydanticUndefined:
                flat[f"{_PREFIX}{name.upper()}"] = _render(value)
    return flat


def _render(value: object) -> str:
    """Render a live default the way the docs write it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | dict):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _normalise_documented(value: str) -> str:
    """Collapse doc formatting that carries no semantic difference."""
    return value.replace('"', "").replace(" ", "").lower()


def test_instruction_docs_documented_defaults_match_the_model() -> None:
    """Every documented default must equal the field's real default.

    Rows whose Default cell is `_(none)_` are skipped (an unset optional), as
    are rows carrying a `DEFAULT_*` constant name in prose — both say "look at
    the code", which is the honest thing for them to say.
    """
    model = _model_defaults()
    documented = _documented_defaults()
    assert documented, "parsed zero default cells from CLAUDE.md"

    unexplained = sorted(
        name
        for name in documented
        if name not in model and name not in _EFFECTIVE_NOT_FIELD_DEFAULT
    )
    assert unexplained == [], (
        "documented default for a name the model walk does not emit — either a "
        f"typo or an unrecorded exemption: {unexplained}"
    )

    mismatches = [
        (name, doc_value, model[name])
        for name, doc_value in documented.items()
        if name in model and _normalise_documented(doc_value) != _normalise_documented(model[name])
    ]
    assert mismatches == [], (
        f"CLAUDE.md documents a default that differs from the Settings field: {mismatches}"
    )


def _documented_row_names() -> frozenset[str]:
    """Every ``MANGOMAS_*`` name that appears as a **config-table row**.

    Deliberately not ``_names_in``, which regexes the whole file: a bare
    mention in a sentence or a code fence satisfies that, and a row does not
    have to exist for it to pass.
    """
    text = _instruction_text()
    return frozenset(_normalize(name) for name, _ in _ROW_RE.findall(text))


def test_every_settings_default_is_documented_in_a_table_row() -> None:
    """The reverse direction, which nothing asserted until now.

    ``test_instruction_docs_documented_defaults_match_the_model`` walks
    *documented → model*: it checks that what the docs claim is true. Nothing
    walked *model → documented*, and its non-vacuity floor is a single row
    (``assert documented``). A trim could therefore delete 95 of the 96 default
    cells and stay green — and a trim is exactly what the AGENTS.md split
    proposes. The field would keep its default, the doc would stop mentioning
    it, and the next session would be told nothing.

    Row presence, not value presence, is the assertion: a name documented as
    ``_(none)_`` is documented (an unset optional is a real answer), but a name
    with no row at all is not.
    """
    model = _model_defaults()
    assert model, "model walk emitted zero defaults — the check would be vacuous"

    rows = _documented_row_names()
    undocumented = sorted(
        name for name in model if name not in rows and name not in _DEFAULT_UNDOCUMENTED_OK
    )
    assert undocumented == [], (
        "these Settings fields have a default but no row in the root instruction "
        f"pair's config tables, so nothing tells a session they exist: {undocumented}. "
        f"Add a row to whichever of {list(ROOT_INSTRUCTION_RELPATHS)} documents that "
        "group, or record the omission in _DEFAULT_UNDOCUMENTED_OK with a reason."
    )
