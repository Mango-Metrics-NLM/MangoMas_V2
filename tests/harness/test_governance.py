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
    assert "src/mangomas/core/orchestrator.py" in governance.PROTECTED_PATHS
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
