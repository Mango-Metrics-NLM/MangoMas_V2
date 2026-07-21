"""Tests for ``mangomas.harness.governance``."""

from __future__ import annotations

import subprocess

import pytest

from mangomas.harness import governance


def _arbitrary_protected_path() -> str:
    """Return any one path the module considers protected (order-independent)."""
    return sorted(governance.PROTECTED_PATHS)[0]


# ── normalize_path ───────────────────────────────────────────────────────────


def test_normalize_path_converts_backslashes() -> None:
    assert governance.normalize_path(r"src\mangomas\core\agent.py") == "src/mangomas/core/agent.py"


def test_normalize_path_strips_leading_dot_slash() -> None:
    assert governance.normalize_path("./src/mangomas/errors.py") == "src/mangomas/errors.py"


def test_normalize_path_preserves_dotgithub() -> None:
    assert governance.normalize_path(".github/agents/backend.agent.md") == (
        ".github/agents/backend.agent.md"
    )


def test_normalize_path_passthrough_for_forward_slash() -> None:
    path = "src/mangomas/agents/chat.py"
    assert governance.normalize_path(path) == path


# ── is_protected_path ────────────────────────────────────────────────────────


def test_is_protected_path_true_for_protected_forward_slash() -> None:
    assert governance.is_protected_path(_arbitrary_protected_path()) is True


def test_is_protected_path_true_for_protected_windows_backslash() -> None:
    windows_path = r".\src\mangomas\core\agent.py"
    assert governance.is_protected_path(windows_path) is True


def test_is_protected_path_false_for_unprotected() -> None:
    assert governance.is_protected_path("src/mangomas/agents/chat.py") is False


# ── has_breaking_change_marker ───────────────────────────────────────────────


def test_has_breaking_change_marker_returns_none_when_absent() -> None:
    assert governance.has_breaking_change_marker("some unrelated diff text") is None


def test_has_breaking_change_marker_finds_canonical_marker() -> None:
    diff = f"+ some line\n+ {governance.BREAKING_CHANGE_MARKER}: reason\n"
    assert governance.has_breaking_change_marker(diff) == governance.BREAKING_CHANGE_MARKER


def test_has_breaking_change_marker_finds_legacy_alias() -> None:
    diff = "+ # approved-breaking-change\n"
    assert governance.has_breaking_change_marker(diff) == "# approved-breaking-change"


# ── read_staged_diff ──────────────────────────────────────────────────────────


def test_read_staged_diff_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeCompleted:
        returncode = 0
        stdout = "+ diff content\n"
        stderr = ""

    def fake_run(*_args: object, **_kwargs: object) -> _FakeCompleted:
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert governance.read_staged_diff("some/path.py") == "+ diff content\n"


def test_read_staged_diff_returns_empty_on_git_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """``read_staged_diff`` swallows git failures and returns an empty string."""

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
    """A missing ``git`` executable (``OSError``/``FileNotFoundError``) fails closed."""

    def fake_run(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("git: command not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert governance.read_staged_diff("some/path.py") == ""
