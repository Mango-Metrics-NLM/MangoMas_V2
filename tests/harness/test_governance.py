"""Tests for ``mangomas.harness.governance`` (ADR-0021 / spec-0017)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangomas.harness import governance

_VALID_GOVERNANCE_TOML = """
[tool.mangomas.governance]
protected_paths = ["src/mangomas/core/agent.py"]
breaking_change_marker_aliases = ["BREAKING-CHANGE", "# approved-breaking-change"]
"""
_MISSING_TABLE_TOML = "[tool.mangomas]\n# no governance table\n"
_EMPTY_PROTECTED_PATHS_TOML = """
[tool.mangomas.governance]
protected_paths = []
breaking_change_marker_aliases = ["BREAKING-CHANGE"]
"""


def test_module_level_constants_match_the_real_pyproject_toml() -> None:
    """The module-level PROTECTED_PATHS is loaded at import time from the
    real repo pyproject.toml — same single source of truth as
    scripts/check_protected_paths.py and scripts/lint_agent_frontmatter.py."""
    assert "src/mangomas/core/orchestrator/__init__.py" in governance.PROTECTED_PATHS
    assert "src/mangomas/core/orchestrator/_client.py" in governance.PROTECTED_PATHS
    assert "BREAKING-CHANGE" in governance.BREAKING_CHANGE_MARKER_ALIASES


def test_load_governance_reads_valid_toml(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_VALID_GOVERNANCE_TOML, encoding="utf-8")
    protected, aliases = governance._load_governance(pyproject)
    assert protected == frozenset({"src/mangomas/core/agent.py"})
    assert aliases == frozenset({"BREAKING-CHANGE", "# approved-breaking-change"})


def test_load_governance_falls_back_on_missing_file(tmp_path: Path) -> None:
    protected, aliases = governance._load_governance(tmp_path / "does-not-exist.toml")
    assert protected == governance._FALLBACK_PROTECTED_PATHS
    assert aliases == governance._FALLBACK_BREAKING_CHANGE_MARKER_ALIASES


def test_load_governance_falls_back_on_missing_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_MISSING_TABLE_TOML, encoding="utf-8")
    protected, aliases = governance._load_governance(pyproject)
    assert protected == governance._FALLBACK_PROTECTED_PATHS
    assert aliases == governance._FALLBACK_BREAKING_CHANGE_MARKER_ALIASES


def test_load_governance_falls_back_on_empty_protected_paths(tmp_path: Path) -> None:
    """Never fall back to an empty set from a *present-but-empty* table
    either — that would silently disable protection."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_EMPTY_PROTECTED_PATHS_TOML, encoding="utf-8")
    protected, _aliases = governance._load_governance(pyproject)
    assert protected == governance._FALLBACK_PROTECTED_PATHS


def test_load_governance_falls_back_on_malformed_toml(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("not [ valid toml", encoding="utf-8")
    protected, aliases = governance._load_governance(pyproject)
    assert protected == governance._FALLBACK_PROTECTED_PATHS
    assert aliases == governance._FALLBACK_BREAKING_CHANGE_MARKER_ALIASES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/mangomas/core/agent.py", True),
        (r".\src\mangomas\core\agent.py", True),
        ("./src/mangomas/core/agent.py", True),
        ("src/mangomas/agents/chat.py", False),
    ],
)
def test_is_protected_path_normalizes_separators(raw: str, expected: bool) -> None:
    assert governance.is_protected_path(raw) is expected


def test_normalize_path_strips_leading_dot_slash_without_destroying_dotdirs() -> None:
    assert governance.normalize_path("./.claude/settings.json") == ".claude/settings.json"


def test_has_breaking_change_marker_finds_the_documented_marker() -> None:
    assert governance.has_breaking_change_marker("diff\nBREAKING-CHANGE\nmore") == "BREAKING-CHANGE"


def test_has_breaking_change_marker_finds_the_legacy_alias() -> None:
    assert governance.has_breaking_change_marker("# approved-breaking-change") is not None


def test_has_breaking_change_marker_returns_none_when_absent() -> None:
    assert governance.has_breaking_change_marker("just an ordinary diff") is None


def test_read_staged_diff_returns_empty_on_git_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess  # noqa: PLC0415 -- local import keeps top-of-file lean

    class _FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository"

    def fake_run(*_args: object, **_kwargs: object) -> _FakeCompleted:
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert governance.read_staged_diff("nonexistent.py") == ""


def test_read_staged_diff_returns_empty_when_git_executable_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess  # noqa: PLC0415 -- local import keeps top-of-file lean

    def fake_run(*_args: object, **_kwargs: object) -> None:
        raise OSError("git: command not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert governance.read_staged_diff("nonexistent.py") == ""


def test_read_staged_diff_returns_real_diff_output() -> None:
    """Smoke test against the real repo — proves the happy path end to end."""
    result = governance.read_staged_diff("pyproject.toml")
    assert isinstance(result, str)


@pytest.mark.parametrize(
    ("text", "expect_match"),
    [
        ("BREAKING-CHANGE: widened protocol", True),
        ("BREAKING-CHANGE", True),
        ("# approved-breaking-change", True),
        # An *added* diff line carries the marker; a *deleted* one does not.
        ("+BREAKING-CHANGE: widened protocol", True),
        ("-BREAKING-CHANGE: widened protocol", False),
        # The two cases a bare `marker in text` substring check gets wrong, and
        # which this module's own docstring promises it handles.
        ("This is NOT a BREAKING-CHANGE, just an internal cleanup.", False),
        ("BREAKING-CHANGEFOO: not the marker", False),
        ("just an ordinary message", False),
    ],
)
def test_has_breaking_change_marker_matches_the_documented_cases(
    text: str, expect_match: bool
) -> None:
    """The in-package copy must honour the same eight cases as its ``scripts/``
    twin. It previously had only two positive assertions, so the two invariants
    its docstring actually claims — rejecting a deleted diff line and rejecting
    the marker mid-sentence — were untested here while being covered in
    ``tests/test_scripts_shared_helpers.py``. The duplication between the two
    implementations is deliberate (``scripts/`` must not import ``mangomas``);
    the asymmetry in their tests was not.
    """
    assert (governance.has_breaking_change_marker(text) is not None) is expect_match


# ── Fallbacks must not drift from the real table (spec-0023 R6) ───────────────


def _pyproject_governance() -> dict[str, list[str]]:
    import tomllib  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    doc = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    table = doc["tool"]["mangomas"]["governance"]
    return {
        "protected_paths": list(table["protected_paths"]),
        "aliases": list(table["breaking_change_marker_aliases"]),
    }


def test_package_fallback_matches_the_pyproject_table() -> None:
    """The hand-maintained fallback must equal the single source of truth.

    `_load_governance` resolves `pyproject.toml` **relative to the cwd** and
    falls back on `OSError`, so importing `mangomas.harness` from anywhere but
    the repo root makes the fallback authoritative — silently, because the
    warning it logs fires at import time, usually before logging is
    configured. A drifted fallback would then under-protect (or over-protect)
    with nothing to catch it.
    """
    table = _pyproject_governance()
    assert frozenset(table["protected_paths"]) == governance._FALLBACK_PROTECTED_PATHS
    assert frozenset(table["aliases"]) == governance._FALLBACK_BREAKING_CHANGE_MARKER_ALIASES


def test_scripts_fallback_matches_the_pyproject_table() -> None:
    """Same pin for the deliberately-duplicated `scripts/` copy.

    `scripts/` must run before `pip install -e .`, so it cannot import
    `mangomas` and keeps its own fallback. The duplication is intentional; the
    two silently disagreeing would not be.
    """
    from tests._script_loader import load_script_module  # noqa: PLC0415

    linter = load_script_module("lint_agent_frontmatter.py")
    table = _pyproject_governance()
    assert frozenset(table["protected_paths"]) == linter._FALLBACK_PROTECTED_PATHS
    assert frozenset(table["aliases"]) == linter._FALLBACK_BREAKING_CHANGE_MARKER_ALIASES


# ── The governance mechanism must protect itself (ADR-0030) ──────────────────


def test_the_governance_surface_protects_itself() -> None:
    """Every file the protected-path mechanism is made of is itself protected.

    A gate whose own definition can be edited without leaving a record in
    ``git log`` is a gate the governed tree can quietly retire. The failure
    message names the offending files rather than reporting ``False``, so a
    contributor who adds a new governance file learns exactly what to do.
    """
    unprotected = governance.unprotected_governance_surface()
    assert unprotected == frozenset(), (
        "these governance files are not in [tool.mangomas.governance] "
        f"protected_paths: {sorted(unprotected)}"
    )


def test_governance_surface_entries_exist_on_disk() -> None:
    """A surface entry naming a moved or deleted file would protect nothing.

    The companion to the containment check above: that one proves the set is
    covered by policy, this one proves the set still describes reality. A
    renamed file would otherwise leave a green gate guarding a path that no
    longer exists.
    """
    root = Path(__file__).resolve().parents[2]
    missing = sorted(path for path in governance.GOVERNANCE_SURFACE if not (root / path).exists())
    assert missing == [], f"GOVERNANCE_SURFACE names files that do not exist: {missing}"


def test_unprotected_governance_surface_reports_a_shrunken_policy() -> None:
    """The helper must actually *detect* an under-protecting policy.

    Mutation proof, mechanised (``mango-mutation-proof``): pass a candidate
    policy with the mechanism's own files removed and assert the helper names
    them. Without this, ``test_the_governance_surface_protects_itself`` could
    pass because the helper always returns an empty set.
    """
    shrunken = frozenset({"src/mangomas/core/agent.py"})

    reported = governance.unprotected_governance_surface(shrunken)

    assert reported == governance.GOVERNANCE_SURFACE


def test_unprotected_governance_surface_normalizes_policy_separators() -> None:
    """A Windows-style policy entry must still count as protecting the file.

    ``PROTECTED_PATHS`` is read from TOML a human edits, and this repo is
    developed on Windows as well as POSIX. Comparing raw strings would report
    a correctly-protected file as unprotected.
    """
    windows_style = frozenset({path.replace("/", "\\") for path in governance.GOVERNANCE_SURFACE})

    assert governance.unprotected_governance_surface(windows_style) == frozenset()


