"""Tests for ``scripts/lint_agent_frontmatter.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import constants
from tests._script_loader import load_script_module

linter = load_script_module("lint_agent_frontmatter.py")


def _arbitrary_protected_path() -> str:
    """Return any one path the linter considers protected (order-independent)."""
    # Picking deterministically from the frozenset keeps tests stable across runs.
    paths: frozenset[str] = linter.PROTECTED_PATHS
    return sorted(paths)[0]


def _arbitrary_unprotected_path() -> str:
    """Return a path the linter is guaranteed to consider unprotected."""
    return "src/mangomas/agents/chat.py"


# ── Schema validation (skills) ────────────────────────────────────────────────


def test_valid_skill_frontmatter_returns_no_errors(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(constants.VALID_SKILL_FRONTMATTER, encoding="utf-8")
    assert linter._validate_skill(str(skill_path)) == []


def test_short_description_skill_returns_error(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(constants.MALFORMED_SKILL_FRONTMATTER_SHORT_DESCRIPTION, encoding="utf-8")
    errors = linter._validate_skill(str(skill_path))
    assert len(errors) == 1
    assert "schema violation" in errors[0]


def test_missing_frontmatter_returns_error(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("just a body, no frontmatter\n", encoding="utf-8")
    errors = linter._validate_skill(str(skill_path))
    assert len(errors) == 1
    assert "missing frontmatter" in errors[0]


# ── Schema validation (agents) ────────────────────────────────────────────────


def test_valid_agent_frontmatter_returns_no_errors(tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(constants.VALID_AGENT_FRONTMATTER, encoding="utf-8")
    assert linter._validate_agent(str(parent_path), [str(parent_path)]) == []


def test_missing_tools_agent_returns_error(tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(constants.MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS, encoding="utf-8")
    errors = linter._validate_agent(str(parent_path), [str(parent_path)])
    assert len(errors) == 1
    assert "schema violation" in errors[0]


def test_agent_unknown_tool_returns_error(tmp_path: Path) -> None:
    invalid = constants.VALID_AGENT_FRONTMATTER.replace("tools: [read, search]", "tools: [delete]")
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(invalid, encoding="utf-8")
    errors = linter._validate_agent(str(parent_path), [str(parent_path)])
    assert len(errors) == 1
    assert "schema violation" in errors[0]


# ── sub_agents resolution ─────────────────────────────────────────────────────


def test_sub_agents_resolves_to_existing_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parent declaring ``sub_agents: [child]`` is valid when the child file exists."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    parent_path = agents_dir / "myparent.agent.md"
    child_dir = agents_dir / "myparent"
    child_dir.mkdir()
    child_path = child_dir / "mychild.agent.md"

    parent_body = constants.VALID_AGENT_FRONTMATTER.replace(
        "---\n\nBody content.\n", "sub_agents:\n  - mychild\n---\n\nBody.\n"
    )
    parent_path.write_text(parent_body, encoding="utf-8")
    child_path.write_text(constants.VALID_AGENT_FRONTMATTER, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    errors = linter._validate_agent(
        ".github/agents/myparent.agent.md",
        [
            ".github/agents/myparent.agent.md",
            ".github/agents/myparent/mychild.agent.md",
        ],
    )
    assert errors == []


def test_sub_agents_missing_child_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent declaring an unresolvable sub_agents slug returns ``missing file``."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    parent_path = agents_dir / "myparent.agent.md"
    parent_body = constants.VALID_AGENT_FRONTMATTER.replace(
        "---\n\nBody content.\n", "sub_agents:\n  - ghost\n---\n\nBody.\n"
    )
    parent_path.write_text(parent_body, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    errors = linter._validate_agent(
        ".github/agents/myparent.agent.md",
        [".github/agents/myparent.agent.md"],
    )
    assert len(errors) == 1
    assert "missing file" in errors[0]


def test_sub_agents_on_child_file_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child file declaring its own sub_agents is rejected — hierarchy is two-deep only."""
    agents_dir = tmp_path / ".github" / "agents" / "myparent"
    agents_dir.mkdir(parents=True)
    child_path = agents_dir / "mychild.agent.md"
    child_body = constants.VALID_AGENT_FRONTMATTER.replace(
        "---\n\nBody content.\n", "sub_agents:\n  - grandchild\n---\n\nBody.\n"
    )
    child_path.write_text(child_body, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    errors = linter._validate_agent(
        ".github/agents/myparent/mychild.agent.md",
        [".github/agents/myparent/mychild.agent.md"],
    )
    assert len(errors) == 1
    assert "only valid on parent" in errors[0]


# ── Protected-path enforcement ────────────────────────────────────────────────


def test_protected_path_check_unprotected_returns_ok() -> None:
    """An unprotected path always returns EXIT_OK."""
    assert linter._check_protected_path(_arbitrary_unprotected_path()) == linter.EXIT_OK


def test_protected_path_without_marker_returns_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected path with no marker in staged diff returns EXIT_PROTECTED."""

    def fake_diff(_path: str) -> str:
        return "no marker here, just code changes\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_PROTECTED


def test_protected_path_with_marker_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected path whose diff contains the marker returns EXIT_OK."""

    def fake_diff(_path: str) -> str:
        return f"some diff\n{linter.BREAKING_CHANGE_MARKER}\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_OK


@pytest.mark.parametrize(
    "protected",
    [
        "src/mangomas/core/agent.py",
        "src/mangomas/core/orchestrator.py",
        "src/mangomas/core/tools.py",
        "src/mangomas/errors.py",
        "src/mangomas/registry.py",
    ],
)
def test_protected_paths_cover_documented_core_contracts(protected: str) -> None:
    """Every core contract documented in CLAUDE.md is gated by the linter."""
    assert protected in linter.PROTECTED_PATHS


def test_protected_path_accepts_legacy_alias_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy ``# approved-breaking-change`` marker is still accepted."""

    def fake_diff(_path: str) -> str:
        return "some diff\n# approved-breaking-change\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_OK


def test_protected_path_windows_backslash_without_marker_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows backslash path must normalize to a protected path and be gated.

    Regression guard: ``str.lstrip('./')`` left backslash paths unrecognised, so
    a ``.\\src\\...\\agent.py`` edit bypassed the hook entirely.
    """

    def fake_diff(_path: str) -> str:
        return "no marker here, just code changes\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    windows_path = r".\src\mangomas\core\agent.py"
    assert linter._check_protected_path(windows_path) == linter.EXIT_PROTECTED


def test_protected_path_windows_backslash_with_marker_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normalized Windows path with the marker in its diff is approved."""

    def fake_diff(_path: str) -> str:
        return f"some diff\n{linter.BREAKING_CHANGE_MARKER}\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    windows_path = r".\src\mangomas\core\agent.py"
    assert linter._check_protected_path(windows_path) == linter.EXIT_OK


def test_staged_diff_returns_empty_on_git_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_staged_diff`` swallows git failures and returns an empty string."""
    import subprocess  # noqa: PLC0415 -- local import keeps top-of-file lean

    class _FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository"

    def fake_run(*_args: object, **_kwargs: object) -> _FakeCompleted:
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert linter._staged_diff("nonexistent.py") == ""


# ── main() integration ────────────────────────────────────────────────────────


def test_main_returns_ok_on_clean_repo() -> None:
    """Running the linter against the actual repo must pass."""
    assert linter.main([]) == linter.EXIT_OK


def test_main_protected_mode_unprotected_path() -> None:
    """``--check-protected-paths`` against an unprotected path returns EXIT_OK."""
    assert linter.main(["--check-protected-paths", _arbitrary_unprotected_path()]) == linter.EXIT_OK


def test_main_schema_failure_returns_exit_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed agent file makes ``main()`` exit with ``EXIT_SCHEMA``."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    skills_dir = tmp_path / ".github" / "skills" / "broken"
    skills_dir.mkdir(parents=True)

    (skills_dir / "SKILL.md").write_text(constants.VALID_SKILL_FRONTMATTER, encoding="utf-8")
    (agents_dir / "broken.agent.md").write_text(
        constants.MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS, encoding="utf-8"
    )

    monkeypatch.chdir(tmp_path)
    assert linter.main([]) == linter.EXIT_SCHEMA
