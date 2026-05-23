"""Tests for ``scripts/lint_agent_frontmatter.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests import constants

# ── Module loader (scripts/ is not a package) ─────────────────────────────────

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "lint_agent_frontmatter.py"


def _load_linter() -> ModuleType:
    """Load the linter module by file path so tests don't depend on PYTHONPATH."""
    spec = importlib.util.spec_from_file_location("_linter_under_test", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


linter = _load_linter()


# ── Schema validation (skills) ────────────────────────────────────────────────


def test_valid_skill_frontmatter_returns_no_errors(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(constants.VALID_SKILL_FRONTMATTER, encoding="utf-8")
    assert linter._validate_skill(str(skill_path)) == []  # noqa: SLF001


def test_short_description_skill_returns_error(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(constants.MALFORMED_SKILL_FRONTMATTER_SHORT_DESCRIPTION, encoding="utf-8")
    errors = linter._validate_skill(str(skill_path))  # noqa: SLF001
    assert len(errors) == 1
    assert "schema violation" in errors[0]


def test_missing_frontmatter_returns_error(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("just a body, no frontmatter\n", encoding="utf-8")
    errors = linter._validate_skill(str(skill_path))  # noqa: SLF001
    assert len(errors) == 1
    assert "missing frontmatter" in errors[0]


# ── Schema validation (agents) ────────────────────────────────────────────────


def test_valid_agent_frontmatter_returns_no_errors(tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(constants.VALID_AGENT_FRONTMATTER, encoding="utf-8")
    assert linter._validate_agent(str(parent_path), [str(parent_path)]) == []  # noqa: SLF001


def test_missing_tools_agent_returns_error(tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(constants.MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS, encoding="utf-8")
    errors = linter._validate_agent(str(parent_path), [str(parent_path)])  # noqa: SLF001
    assert len(errors) == 1
    assert "schema violation" in errors[0]


def test_agent_unknown_tool_returns_error(tmp_path: Path) -> None:
    invalid = constants.VALID_AGENT_FRONTMATTER.replace("tools: [read, search]", "tools: [delete]")
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(invalid, encoding="utf-8")
    errors = linter._validate_agent(str(parent_path), [str(parent_path)])  # noqa: SLF001
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
    errors = linter._validate_agent(  # noqa: SLF001
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
    errors = linter._validate_agent(  # noqa: SLF001
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
    errors = linter._validate_agent(  # noqa: SLF001
        ".github/agents/myparent/mychild.agent.md",
        [".github/agents/myparent/mychild.agent.md"],
    )
    assert len(errors) == 1
    assert "only valid on parent" in errors[0]


# ── Protected-path enforcement ────────────────────────────────────────────────


def test_protected_path_check_unprotected_returns_ok() -> None:
    """An unprotected path always returns EXIT_OK."""
    assert (
        linter._check_protected_path("src/mangomas/agents/chat.py")  # noqa: SLF001
        == linter.EXIT_OK
    )


def test_protected_path_without_marker_returns_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected path with no marker in staged diff returns EXIT_PROTECTED."""

    def fake_diff(_path: str) -> str:
        return "no marker here, just code changes\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert (
        linter._check_protected_path("src/mangomas/core/agent.py")  # noqa: SLF001
        == linter.EXIT_PROTECTED
    )


def test_protected_path_with_marker_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected path whose diff contains the marker returns EXIT_OK."""

    def fake_diff(_path: str) -> str:
        return f"some diff\n{linter.BREAKING_CHANGE_MARKER}\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert (
        linter._check_protected_path("src/mangomas/core/agent.py")  # noqa: SLF001
        == linter.EXIT_OK
    )


# ── main() integration ────────────────────────────────────────────────────────


def test_main_returns_ok_on_clean_repo() -> None:
    """Running the linter against the actual repo must pass."""
    assert linter.main([]) == linter.EXIT_OK


def test_main_protected_mode_unprotected_path() -> None:
    """``--check-protected-paths`` against an unprotected path returns EXIT_OK."""
    assert linter.main(["--check-protected-paths", "src/mangomas/agents/chat.py"]) == linter.EXIT_OK
