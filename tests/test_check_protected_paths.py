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
    # ADR-0030: the approval line now reports *how* the path was approved
    # (scoped vs unscoped) and tags each path, so a run that passed because an
    # unrelated marker happened to be in range is distinguishable from one
    # whose marker actually named this file.
    assert f"  - {_PROTECTED_FILE} [approved]" in out
    assert "Approved by an unscoped marker in a commit message. OK." in out

    repo2_base = tmp_path / "repo2"
    repo2_base.mkdir()
    repo2 = _init_repo(repo2_base)
    _commit_touching(repo2, _PROTECTED_FILE, "refactor core agent, no marker")
    assert _run_in_repo(monkeypatch, repo2) == check_protected_paths.EXIT_MISSING_MARKER
    err = capsys.readouterr().err
    assert "FAIL: these protected paths changed with no approving marker:" in err
    assert _PROTECTED_FILE in err
    assert "BREAKING-CHANGE: <path>" in err


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


# ── The policy comes from the base ref, not the branch under test (ADR-0030) ──

_SHRUNKEN_GOVERNANCE_TOML: Final[str] = f"""
[tool.mangomas.governance]
protected_paths = ["{_UNPROTECTED_FILE}"]
breaking_change_marker_aliases = ["BREAKING-CHANGE", "# approved-breaking-change"]
"""


def _commit_shrinking_the_policy(repo: Path, message: str) -> None:
    """Rewrite the policy so the branch is no longer judged by the base's set.

    Swaps the protected entry rather than emptying it: an empty table is
    already rejected as malformed, so the realistic evasion keeps the table
    well-formed and simply drops the file it is about to edit.
    """
    (repo / "pyproject.toml").write_text(_SHRUNKEN_GOVERNANCE_TOML, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def test_gate_uses_the_base_refs_policy_not_the_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A branch cannot shrink the protected set it is judged by.

    The whole point of the gate: reading the policy from the same commit it is
    checking lets one PR both remove a file from ``protected_paths`` and edit
    it, marker-free. Reading the base ref's table closes that ordering hole.
    """
    repo = _init_repo(tmp_path)
    _commit_shrinking_the_policy(repo, "chore: tidy the governance table")
    _commit_touching(repo, _PROTECTED_FILE, "refactor: no marker here")

    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_MISSING_MARKER


def test_a_marker_still_approves_a_change_under_the_base_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base-ref read must not make the gate unsatisfiable.

    Companion direction to the test above (``mango-mutation-proof``: a guard
    that only ever fails is as useless as one that only ever passes). Same
    shrunken policy, but the commit carries the marker — the gate must accept.
    """
    repo = _init_repo(tmp_path)
    _commit_shrinking_the_policy(repo, "chore: tidy the governance table")
    _commit_touching(repo, _PROTECTED_FILE, "refactor: deliberate\n\nBREAKING-CHANGE: reviewed")

    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK


def test_worktree_policy_is_the_fallback_when_the_base_ref_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreadable base-ref policy degrades to the worktree, loudly.

    A base branch predating the governance table (or a shallow clone that
    cannot resolve it) must not hard-fail the gate — but a silent downgrade
    would reintroduce exactly the hole this change closes, so the fallback
    announces itself on stderr.
    """
    repo = _init_repo(tmp_path, governance=_MALFORMED_GOVERNANCE_TOML)
    (repo / "pyproject.toml").write_text(_GOVERNANCE_TOML, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: introduce the governance table")
    _commit_touching(repo, _PROTECTED_FILE, "refactor: no marker here")

    exit_code = _run_in_repo(monkeypatch, repo)

    assert exit_code == check_protected_paths.EXIT_MISSING_MARKER
    assert "falling back to the working tree" in capsys.readouterr().err


# ── A marker approves the path it names, not every protected path (ADR-0030) ──

_SECOND_PROTECTED_FILE: Final[str] = "src/mangomas/errors.py"
_TWO_PROTECTED_TOML: Final[str] = f"""
[tool.mangomas.governance]
protected_paths = ["{_PROTECTED_FILE}", "{_SECOND_PROTECTED_FILE}"]
breaking_change_marker_aliases = ["BREAKING-CHANGE", "# approved-breaking-change"]
"""


def _init_two_protected_repo(tmp_path: Path) -> Path:
    """A repo whose base policy protects two files, both present."""
    repo = _init_repo(tmp_path, governance=_TWO_PROTECTED_TOML)
    second = repo / _SECOND_PROTECTED_FILE
    second.parent.mkdir(parents=True, exist_ok=True)
    second.write_text("# base content\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: add the second protected file")
    _git(repo, "branch", "-qf", "base", "HEAD")
    return repo


def test_marker_naming_one_file_does_not_approve_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scoped marker approves only the path it names.

    An unscoped marker is a scope-free token: one ``BREAKING-CHANGE`` anywhere
    in the range used to approve edits to every protected path in it,
    including ones added in later commits the approver never saw.
    """
    repo = _init_two_protected_repo(tmp_path)
    _commit_touching(
        repo, _PROTECTED_FILE, f"refactor: reviewed\n\nBREAKING-CHANGE: {_PROTECTED_FILE} — agreed"
    )
    _commit_touching(repo, _SECOND_PROTECTED_FILE, "refactor: unrelated, unapproved")

    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_MISSING_MARKER


def test_one_scoped_marker_per_touched_path_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Naming every touched path satisfies the gate.

    The other direction: the scoped form has to be *usable*, or contributors
    will reach for the bare marker forever.
    """
    repo = _init_two_protected_repo(tmp_path)
    _commit_touching(
        repo, _PROTECTED_FILE, f"refactor: reviewed\n\nBREAKING-CHANGE: {_PROTECTED_FILE} — agreed"
    )
    _commit_touching(
        repo,
        _SECOND_PROTECTED_FILE,
        f"refactor: reviewed\n\nBREAKING-CHANGE: {_SECOND_PROTECTED_FILE} — agreed",
    )

    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK


def test_a_bare_marker_still_approves_every_touched_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The historical unscoped form keeps working.

    Back-compat is the reason this is additive rather than a cliff: every
    marker in this repo's history is unscoped, and a gate that rejected them
    would fail on any branch that merges an older one.
    """
    repo = _init_two_protected_repo(tmp_path)
    _commit_touching(repo, _PROTECTED_FILE, "refactor: reviewed\n\nBREAKING-CHANGE: both agreed")
    _commit_touching(repo, _SECOND_PROTECTED_FILE, "refactor: rides the same approval")

    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK


def test_a_scoped_marker_naming_an_unrelated_path_approves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Naming some *other* protected path must not act as a bare marker.

    Without this, the scope parser could fall back to "unscoped" whenever it
    did not recognise the path — turning every typo into a universal approval,
    which is the failure mode the marker-alias rule already warns about.
    """
    repo = _init_two_protected_repo(tmp_path)
    _commit_touching(
        repo,
        _PROTECTED_FILE,
        f"refactor: mis-scoped\n\nBREAKING-CHANGE: {_SECOND_PROTECTED_FILE} — wrong file",
    )

    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_MISSING_MARKER


def test_a_base_ref_without_a_policy_file_falls_back_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base ref with no ``pyproject.toml`` at all degrades to the worktree.

    Distinct from the malformed-policy case: there git *succeeds* and parsing
    fails, here ``git show`` itself fails. Both must land on the same loud
    fallback — a base branch predating the file, or a clone too shallow to
    resolve it, cannot be allowed to hard-fail the gate *or* to quietly skip
    it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    protected = repo / _PROTECTED_FILE
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("# base content\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit with no pyproject.toml")
    _git(repo, "branch", "-q", "base")
    (repo / "pyproject.toml").write_text(_GOVERNANCE_TOML, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: introduce the governance table")
    _commit_touching(repo, _PROTECTED_FILE, "refactor: no marker here")

    exit_code = _run_in_repo(monkeypatch, repo)

    assert exit_code == check_protected_paths.EXIT_MISSING_MARKER
    err = capsys.readouterr().err
    assert "could not be read from git" in err
    assert "falling back to the working tree" in err


def test_policy_blob_path_survives_a_path_outside_the_tree(tmp_path: Path) -> None:
    """An unrelatable path is passed to git as-is rather than raising.

    ``Path.relative_to`` raises ``ValueError`` for a path that is not under the
    cwd (and, on Windows, for one on another drive). Returning it unchanged
    lets git produce the diagnostic instead of the gate dying on a traceback.
    """
    outside = tmp_path / "elsewhere" / "pyproject.toml"

    assert check_protected_paths._policy_blob_path(outside).endswith("pyproject.toml")


def test_a_marker_naming_a_path_the_base_does_not_protect_reads_as_unscoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marker naming a not-yet-protected file approves broadly, not narrowly.

    The sharp edge of "scoped only when the first token is an *exact* protected
    path", and worth pinning rather than discovering. A commit that widens
    ``protected_paths`` and names one of the *newly* protected files in its
    marker is judged against the **base** policy (ADR-0030), which does not
    contain that path yet — so the marker reads as prose and approves every
    touched path in the range.

    This is the intended direction of failure: broad-and-visible in a line a
    reviewer can see, never silently-narrowed-to-nothing. It is also why
    widening the protected set is a reviewable event in its own right.
    """
    repo = _init_two_protected_repo(tmp_path)
    _commit_touching(
        repo,
        _PROTECTED_FILE,
        "chore: widen governance\n\nBREAKING-CHANGE: docs/not-protected.md — new scope",
    )

    assert _run_in_repo(monkeypatch, repo) == check_protected_paths.EXIT_OK
