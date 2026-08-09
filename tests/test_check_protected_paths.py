"""Tests for ``scripts/check_protected_paths.py`` (ADR-0021 / spec-0017).

Exercises the real git plumbing against a throwaway repository built in
``tmp_path`` rather than mocking ``subprocess`` — this script's entire job is
to reason about git history correctly, so a mocked test would prove nothing
about the one thing that matters.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

import pytest

from tests._script_loader import load_script_module

check_protected_paths = load_script_module("check_protected_paths.py")

_PROTECTED_FILE: Final[str] = "src/mangomas/core/agent.py"
_UNPROTECTED_FILE: Final[str] = "src/mangomas/agents/chat.py"
_GOVERNANCE_TOML: Final[str] = f"""
[tool.mangomas.governance]
protected_paths = ["{_PROTECTED_FILE}"]
breaking_change_marker_aliases = ["BREAKING-CHANGE", "# approved-breaking-change"]
"""
_MALFORMED_GOVERNANCE_TOML: Final[str] = "[tool.mangomas]\n# no governance table\n"
_EMPTY_PROTECTED_PATHS_TOML: Final[str] = """
[tool.mangomas.governance]
protected_paths = []
breaking_change_marker_aliases = ["BREAKING-CHANGE"]
"""
_EMPTY_MARKER_ALIASES_TOML: Final[str] = f"""
[tool.mangomas.governance]
protected_paths = ["{_PROTECTED_FILE}"]
breaking_change_marker_aliases = []
"""


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(tmp_path: Path, *, governance: str = _GOVERNANCE_TOML) -> Path:
    """Init a repo with a base commit carrying *governance* + a protected file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "pyproject.toml").write_text(governance, encoding="utf-8")
    protected = repo / _PROTECTED_FILE
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("# base content\n", encoding="utf-8")
    unprotected = repo / _UNPROTECTED_FILE
    unprotected.parent.mkdir(parents=True, exist_ok=True)
    unprotected.write_text("# base content\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    _git(repo, "branch", "-q", "base")
    return repo


def _commit_touching(repo: Path, path: str, message: str) -> None:
    target = repo / path
    target.write_text(target.read_text(encoding="utf-8") + "# change\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _run_in_repo(monkeypatch: pytest.MonkeyPatch, repo: Path, *, base_ref: str = "base"):
    """Run the check with cwd set to *repo* — its git subprocess calls rely
    on the working directory rather than accepting one explicitly.

    No return annotation: ``check_protected_paths`` is loaded dynamically via
    ``load_script_module`` (returns ``ModuleType``), so mypy sees every
    attribute access on it as ``Any`` — matching the existing pattern in
    ``tests/test_lint_agent_frontmatter.py``, which never wraps such calls in
    an explicitly-typed helper.
    """
    monkeypatch.chdir(repo)
    return check_protected_paths.check(base_ref, "HEAD", repo / "pyproject.toml")


def test_no_protected_path_changed_returns_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    _commit_touching(repo, _UNPROTECTED_FILE, "touch unprotected file only")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK


def test_protected_path_changed_without_marker_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent, no marker")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_MISSING_MARKER


def test_protected_path_changed_with_marker_in_commit_message_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    message = "refactor core agent\n\nBREAKING-CHANGE: widened protocol"
    _commit_touching(repo, _PROTECTED_FILE, message)
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK


def test_legacy_alias_marker_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent\n\n# approved-breaking-change")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK


def test_marker_in_a_different_commit_in_range_still_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker only needs to appear *somewhere* in the commit range, not
    in the same commit that touched the protected path — matching the old
    diff-based check's permissiveness (any diff content, any hunk)."""
    repo = _init_repo(tmp_path)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent, no marker here")
    _commit_touching(repo, _UNPROTECTED_FILE, "separate commit\n\nBREAKING-CHANGE: see above")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK


def test_marker_only_in_diff_body_not_commit_message_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the exact defect this script replaces: the old
    ``marker in diff`` check on ``lint_agent_frontmatter.py`` would pass if
    the string appeared anywhere in the diff (including in a deleted line of
    ordinary code). This script must not be fooled by file *content* — only
    a commit *message* counts."""
    repo = _init_repo(tmp_path)
    target = repo / _PROTECTED_FILE
    target.write_text(
        target.read_text(encoding="utf-8") + "# BREAKING-CHANGE appears here as a code comment\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "refactor core agent, ordinary message")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_MISSING_MARKER


def test_marker_text_embedded_in_prose_does_not_count_as_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: a bare substring test (``marker in messages``) would
    treat a commit message explaining that a change is *not* breaking as an
    approval, since the literal marker text still appears mid-sentence —
    exactly the phrasing a developer would naturally write. Matching must be
    anchored to the start of a line (a ``Marker:``-style trailer), not any
    occurrence anywhere in the message."""
    repo = _init_repo(tmp_path)
    message = "refactor core agent\n\nThis is NOT a BREAKING-CHANGE, just an internal cleanup."
    _commit_touching(repo, _PROTECTED_FILE, message)
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_MISSING_MARKER


def test_unresolvable_base_ref_returns_git_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent")
    assert (
        _run_in_repo(monkeypatch, repo, base_ref="does-not-exist")
        == check_protected_paths.EXIT_GIT_ERROR
    )


def test_missing_governance_table_returns_git_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path, governance=_MALFORMED_GOVERNANCE_TOML)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_GIT_ERROR


def test_present_but_empty_protected_paths_returns_git_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A *present-but-empty* ``protected_paths = []`` is a distinct failure
    mode from the table being missing entirely (test above) — both must
    fail loudly rather than silently gating nothing, since an empty set
    would make every branch pass regardless of what it touches."""
    repo = _init_repo(tmp_path, governance=_EMPTY_PROTECTED_PATHS_TOML)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_GIT_ERROR


def test_present_but_empty_marker_aliases_returns_git_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric case: an empty ``breaking_change_marker_aliases = []``
    would make no commit message ever qualify as an approval, silently
    turning the gate into a permanent, unresolvable block instead of a
    loud config error."""
    repo = _init_repo(tmp_path, governance=_EMPTY_MARKER_ALIASES_TOML)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_GIT_ERROR


def test_missing_git_executable_returns_git_error_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``subprocess.run`` raises ``FileNotFoundError`` (an ``OSError``) when
    the ``git`` executable itself can't be found — a different failure mode
    than git running and exiting non-zero. Both must land on
    ``EXIT_GIT_ERROR``, not an uncaught exception that crashes the gate."""
    repo = _init_repo(tmp_path)
    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent")

    real_run = subprocess.run

    def _raise_file_not_found(args, **kwargs):
        if args[0] == "git":
            raise FileNotFoundError("git: command not found")
        return real_run(args, **kwargs)

    monkeypatch.setattr(check_protected_paths.subprocess, "run", _raise_file_not_found)
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_GIT_ERROR


def test_main_wires_cli_args_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    _commit_touching(repo, _UNPROTECTED_FILE, "touch unprotected file only")
    monkeypatch.chdir(repo)
    exit_code = check_protected_paths.main(
        ["--base-ref", "base", "--head-ref", "HEAD", "--pyproject", "pyproject.toml"]
    )
    assert exit_code == check_protected_paths.EXIT_OK


def test_main_wires_a_non_default_pyproject_path_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test above passes '--pyproject pyproject.toml', identical to the
    script's own default — it would pass even if --pyproject were silently
    ignored. Renaming the governance file and pointing --pyproject at the
    new name proves the CLI argument is actually threaded through, not
    coincidentally satisfied by the default."""
    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").rename(repo / "governance.toml")
    _commit_touching(repo, _UNPROTECTED_FILE, "touch unprotected file only")
    monkeypatch.chdir(repo)
    exit_code = check_protected_paths.main(
        ["--base-ref", "base", "--head-ref", "HEAD", "--pyproject", "governance.toml"]
    )
    assert exit_code == check_protected_paths.EXIT_OK


def test_check_prints_distinct_diagnostics_for_each_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No test in this file previously captured stdout/stderr — ``check()``
    prints meaningfully different diagnostic text for situations that share
    an exit code space (a passing run vs. an approved protected change both
    return EXIT_OK), so an assertion on exit code alone can't distinguish a
    passing check for the *right* reason from one that degraded silently."""
    repo = _init_repo(tmp_path)

    _commit_touching(repo, _UNPROTECTED_FILE, "touch unprotected file only")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK
    assert "No protected core contracts changed. OK." in capsys.readouterr().out

    _commit_touching(repo, _PROTECTED_FILE, "refactor core agent\n\nBREAKING-CHANGE: widened")
    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK
    out = capsys.readouterr().out
    assert "Protected core contracts changed on this branch:" in out
    assert _PROTECTED_FILE in out
    assert "Found approval marker 'BREAKING-CHANGE' in a commit message. OK." in out

    repo2_base = tmp_path / "repo2"
    repo2_base.mkdir()
    repo2 = _init_repo(repo2_base)
    _commit_touching(repo2, _PROTECTED_FILE, "refactor core agent, no marker")
    assert _run_in_repo(monkeypatch, repo2) == check_protected_paths.EXIT_MISSING_MARKER
    err = capsys.readouterr().err
    assert "FAIL: no commit in this range carries a BREAKING-CHANGE marker." in err


def test_real_repo_pyproject_has_governance_table() -> None:
    """The real ``pyproject.toml`` (not a fixture) must carry a valid,
    non-empty governance table — a config regression here would silently
    turn the CI gate into a permanent no-op."""
    from pathlib import Path as _Path  # noqa: PLC0415 -- avoids polluting module import order

    repo_root = _Path(__file__).resolve().parent.parent
    protected_paths, marker_aliases = check_protected_paths._load_governance(
        repo_root / "pyproject.toml"
    )
    assert "src/mangomas/core/orchestrator.py" in protected_paths
    assert "BREAKING-CHANGE" in marker_aliases
